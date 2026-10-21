"""A node's id where its path belongs is refused, naming the path (#514).

Trees are keyed by path since #439: a node below the roots is ``"CA.ON"``,
not ``"ON"``. Code written before then still passes the id, and every walk
answered it as *absent* — ``broader`` ``None``, ``narrower`` empty — which
reads as a root or a leaf rather than as the stale call it is. `depth_of`
alone raised, only because it must return an int.

A key that is nobody's id still means absent: the refusal is for the one
mistake #439 made likely, not a change to what an unknown path means.
"""

from collections.abc import Callable

import pytest

import sdmxlib as sl
from sdmxlib.model.category import Category, CategoryScheme
from sdmxlib.model.codelist import Code
from sdmxlib.model.collections import ItemList
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.query import Query
from sdmxlib.storage.lazy import LazyHierarchy
from tests.support.narrow import present

pytestmark = pytest.mark.unit


def _code(code_id: str) -> Ref[Code]:
    urn = SdmxUrn.make(
        Code, package="codelist", artefact_class="Code", agency="AG", id="CL_GEO", version="1.0", item_id=code_id
    )
    return Ref[Code].unresolved(urn)


def _hierarchy() -> Hierarchy:
    """``ON`` under ``CA`` and nowhere else; ``CA`` a root."""
    return Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "Geo"},
        tree=[
            HierarchicalCode(
                id="CA",
                code=_code("CA"),
                children=[
                    HierarchicalCode(
                        id="ON", code=_code("ON"), children=[HierarchicalCode(id="OTT", code=_code("OTT"))]
                    )
                ],
            )
        ],
    )


def _categories() -> CategoryScheme:
    return CategoryScheme(
        id="CS",
        maintainer="AG",
        name={"en": "Themes"},
        categories=[
            Category(
                id="CA",
                name={"en": "Canada"},
                categories=[
                    Category(id="ON", name={"en": "Ontario"}, categories=[Category(id="OTT", name={"en": "Ottawa"})])
                ],
            )
        ],
    )


#: Every verb that takes a path. `get` is not here: it documents ``None`` for absent.
_VERBS = ["broader", "narrower", "broader_transitive", "narrower_transitive", "depth_of", "__getitem__"]

type _Tree = Hierarchy | CategoryScheme | LazyHierarchy


@pytest.fixture(params=["hierarchy", "category-scheme", "lazy-hierarchy"])
def tree(request: pytest.FixtureRequest):
    if request.param == "hierarchy":
        yield _hierarchy()
    elif request.param == "category-scheme":
        yield _categories()
    else:
        with sl.LocalRegistry(":memory:") as reg:
            reg.add(_hierarchy())
            lazy = present(reg.get(_hierarchy().urn))
            assert isinstance(lazy, LazyHierarchy)
            yield lazy


def _call(tree: _Tree, verb: str, key: str) -> object:
    walk: Callable[[str], object] = getattr(tree, verb)
    return walk(key)


@pytest.mark.parametrize("verb", _VERBS)
@pytest.mark.parametrize("key", ["ON", "OTT"])
def test_a_node_id_is_refused_naming_its_path(tree: _Tree, verb: str, key: str) -> None:
    expected = "'CA.ON'" if key == "ON" else "'CA.ON.OTT'"
    with pytest.raises(KeyError, match=rf"{key!r} is a node id, not a path .* It is at {expected}; by_id"):
        _call(tree, verb, key)


@pytest.mark.parametrize("verb", ["broader", "narrower", "broader_transitive", "narrower_transitive"])
def test_a_key_that_is_nobodys_id_is_still_absent(tree: _Tree, verb: str) -> None:
    """The discrimination check: absent means empty, as before — only a node's id is refused."""
    found = _call(tree, verb, "NOWHERE")
    assert found is None or _size(found) == 0


@pytest.mark.parametrize("verb", _VERBS)
def test_the_path_and_a_roots_bare_id_are_answered(tree: _Tree, verb: str) -> None:
    """A root's path is its id, so a bare id is refused only below the roots."""
    _call(tree, verb, "CA.ON")
    _call(tree, verb, "CA")


def test_the_answers_are_the_paths_answers() -> None:
    h = _hierarchy()
    assert present(h.broader("CA.ON")).id == "CA"
    assert [n.id for n in h.narrower("CA.ON")] == ["OTT"]


def test_a_codelist_is_keyed_by_id_and_unaffected() -> None:
    cl = sl.Codelist(
        id="CL",
        maintainer="AG",
        name={"en": "c"},
        codes=[sl.Code(id="CA", name={"en": "Canada"}), sl.Code(id="ON", name={"en": "Ontario"}, parent_id="CA")],
    )
    assert present(cl.broader("ON")).id == "CA"


def _size(found: object) -> int:
    """How many a walk found — an eager `ItemList`, or a lazy walk's `Query`."""
    if isinstance(found, Query):
        return found.count()
    assert isinstance(found, ItemList)
    return len(found)
