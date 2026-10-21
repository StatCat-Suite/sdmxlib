"""A field marked `derived_view()` must hide nothing (#383).

`_refshape.walk_fields` skips fields carrying the `DERIVED_VIEW` marker, so a
`Ref`-hunting walk does not enter the same objects twice — `Hierarchy._codes`
is `_flatten_nodes(self.tree)`, the *same* node objects, and walking both meant
499,138 wasted calls on `H_CP_GEO_FED`.

That is safe only while the marker means what it says. Its contract is
**reachability, not privacy**:

> a marked field contains nothing that an unmarked field does not already reach.

Nothing about `_`-prefixing or `init=False` implies that. A private field that
held an *independent* edge and got marked would make every `Ref` in it
invisible to resolution, interning and validation at once — silently, since the
objects would still be there and only their refs would stop resolving.

So the marker is checked rather than trusted. These tests walk a populated
graph and assert, for every marked field, that each object it holds is also
reachable without it.
"""

import pathlib

import attrs
import pytest

from sdmxlib.model._refshape import DERIVED_VIEW, walk_fields
from sdmxlib.model.collections import ArtefactList, ItemList

pytestmark = pytest.mark.unit

#: The same reader fixtures `test_refshape_agrees_with_reflection` uses, for the
#: same reason: they populate corners a hand-built graph would miss.
_FIXTURES = [
    ("sdmx_ml21", "complete.xml"),
    ("sdmx_ml21", "with_hierarchy.xml"),
    ("sdmx_ml21", "with_metadata_structure.xml"),
    ("sdmx_ml30", "complete.xml"),
    ("sdmx_ml30", "with_categories.xml"),
    ("sdmx_ml30", "with_multiple_hierarchies.xml"),
]


@pytest.fixture(scope="module")
def artefacts() -> list[object]:
    from sdmxlib.formats import parse

    root = pathlib.Path(__file__).parent.parent / "fixtures"
    out: list[object] = []
    for folder, name in _FIXTURES:
        path = root / folder / name
        if path.is_file():
            out.extend(parse(path.read_bytes()).all_artefacts())
    return out


def _members(value: object) -> list[object]:
    if isinstance(value, (ItemList, ArtefactList)):
        return list(value)
    if attrs.has(type(value)) and not isinstance(value, type):
        return [value]
    return []


def _reachable_without_derived(obj: object) -> set[int]:
    """Every attrs object reachable from *obj* through unmarked fields only."""
    seen: set[int] = set()
    stack = [obj]
    while stack:
        current = stack.pop()
        if id(current) in seen or not attrs.has(type(current)):
            continue
        seen.add(id(current))
        for name in walk_fields(type(current)):
            stack.extend(_members(getattr(current, name, None)))
    return seen


def _marked_fields(cls: type) -> list[str]:
    return [f.name for f in attrs.fields(cls) if f.metadata.get(DERIVED_VIEW)]


def test_the_fixtures_actually_contain_marked_fields(artefacts):
    """Without a marked field in the graph the next test proves nothing."""
    found = {
        f"{type(a).__qualname__}.{name}"
        for a in artefacts
        for name in _marked_fields(type(a))
        if getattr(a, name, None)
    }
    assert found, "no populated derived-view field in the fixture graph — the check below is vacuous"


def test_every_marked_field_holds_only_already_reachable_objects(artefacts):
    """The marker's contract, checked on a populated graph.

    An object in a marked field that is *not* reachable through the unmarked
    ones is an edge the marker has hidden — and hidden from resolution,
    interning and validation simultaneously.
    """
    problems: list[str] = []
    seen: set[int] = set()

    def visit(obj: object) -> None:
        if id(obj) in seen or not attrs.has(type(obj)):
            return
        seen.add(id(obj))
        cls = type(obj)
        members: list[object] = []
        marked = _marked_fields(cls)
        if marked:
            reachable = _reachable_without_derived(obj)
            problems.extend(
                f"{cls.__qualname__}.{name} hides {type(member).__qualname__}"
                for name in marked
                for member in _members(getattr(obj, name, None))
                if id(member) not in reachable
            )
        for name in walk_fields(cls):
            members.extend(_members(getattr(obj, name, None)))
        for member in members:
            visit(member)

    for artefact in artefacts:
        visit(artefact)
    assert not problems, f"derived-view fields hiding unreachable objects: {sorted(set(problems))}"


def test_a_marked_field_that_hid_something_would_be_caught():
    """The check above discriminates — asserted, not assumed.

    Builds the failure the marker makes possible: a field marked derived whose
    contents are reachable *nowhere else*. If this passed, the test above would
    be decoration.
    """
    from sdmxlib.model._refshape import derived_view

    @attrs.define
    class Leaf:
        name: str = ""

    @attrs.define
    class Holder:
        real: list[object] = attrs.field(factory=list)
        hidden: list[object] = attrs.field(factory=list, metadata=derived_view())

    attrs.resolve_types(Holder)
    only_in_hidden = Leaf(name="unreachable")
    holder = Holder(real=[], hidden=[only_in_hidden])

    assert "hidden" not in walk_fields(Holder)
    assert id(only_in_hidden) not in _reachable_without_derived(holder)


def test_the_marked_fields_are_the_ones_expected():
    """A static inventory, so a *new* marker is a review prompt.

    The marker silences an edge for three separate walkers at once. Adding one
    should be a decision someone made, not something that arrives with a
    copy-pasted field definition.
    """
    from sdmxlib.model.category import CategoryScheme
    from sdmxlib.model.hierarchy import Hierarchy
    from sdmxlib.model.metadatastructure import MetadataStructure

    assert _marked_fields(Hierarchy) == ["_tree"]
    assert _marked_fields(CategoryScheme) == ["_tree"]
    assert _marked_fields(MetadataStructure) == ["_tree"]
