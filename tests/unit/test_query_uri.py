"""`uri` is a query path on every relation the store keeps it for (#444).

#442 kept `uri` on every item table and the artefact spine, and `collect()`
returned it, but no path reached it: filtering on it meant collecting and
filtering in Python. Each root's `uri` is checked against the model it was
stored from — the maximal fixture, which sets one nearly everywhere — so a
path wired to the wrong column fails here. `q.code.uri` and `q.hcode.uri` are
also differential properties against the oracles.
"""

from collections.abc import Callable, Iterable, Iterator

import pytest

import sdmxlib as sl
from sdmxlib.model.hierarchy import HierarchicalCode
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataAttribute
from sdmxlib.query.accessors import q
from tests.support import maximal

pytestmark = pytest.mark.unit

type _Pairs = set[tuple[str, str | None]]


def _nodes(nodes: Iterable[HierarchicalCode]) -> Iterator[HierarchicalCode]:
    for node in nodes:
        yield node
        yield from _nodes(node.children)


def _attributes(attributes: Iterable[MetadataAttribute]) -> Iterator[MetadataAttribute]:
    for attribute in attributes:
        yield attribute
        yield from _attributes(attribute.children)


#: Each root, and the (id, uri) pairs its rows hold, read off the model.
_EXPECTED: dict[str, Callable[[StructureMessage], _Pairs]] = {
    "codelist": lambda m: {(cl.id, cl.uri) for cl in m.codelists},
    "dsd": lambda m: {(d.id, d.uri) for d in m.data_structures},
    "code": lambda m: {(c.id, c.uri) for cl in m.codelists for c in cl.codes},
    "concept": lambda m: {(c.id, c.uri) for cs in m.concept_schemes for c in cs.concepts},
    "agency": lambda m: {(a.id, a.uri) for s in m.agency_schemes for a in s.agencies},
    "data_provider": lambda m: {(p.id, p.uri) for s in m.data_provider_schemes for p in s.providers},
    "metadata_provider": lambda m: {(p.id, p.uri) for s in m.metadata_provider_schemes for p in s.providers},
    "group": lambda m: {(g.id, g.uri) for d in m.data_structures for g in d.groups or ()},
    "hcode": lambda m: {(n.id, n.uri) for h in m.hierarchies for n in _nodes(h.tree)},
    "metadata_attribute": lambda m: {(a.id, a.uri) for s in m.metadata_structures for a in _attributes(s.attributes)},
}


@pytest.fixture(scope="module")
def stored() -> Iterator[tuple[sl.LocalRegistry, StructureMessage]]:
    msg = maximal.message()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(msg.all_artefacts())
        yield reg, msg


@pytest.mark.parametrize("root_name", sorted(_EXPECTED))
def test_each_root_selects_the_uri_it_stored(stored: tuple[sl.LocalRegistry, StructureMessage], root_name: str) -> None:
    reg, msg = stored
    root = getattr(q, root_name)
    rows = reg.query(root).select(id=root.id, uri=root.uri).collect().to_dicts()
    expected = _EXPECTED[root_name](msg)
    assert any(uri is not None for _, uri in expected), "the fixture sets a uri here, so the check can fail"
    assert {(r["id"], r["uri"]) for r in rows} == expected


def test_a_uri_filters(stored: tuple[sl.LocalRegistry, StructureMessage]) -> None:
    reg, _ = stored
    assert reg.query(q.code).filter(q.code.uri == "https://example.org/uri/code-parent").ids() == ["PARENT"]
    assert reg.query(q.codelist).filter(q.codelist.uri.str.ends_with("/CL")).ids() == ["CL"]
    assert reg.query(q.hcode).filter(q.hcode.level.uri.is_not_null()).count() >= 1
