"""A hierarchy's levels are a chain, and a node sits at its level's depth or below it (#455).

SDMX-ML gives a hierarchy one ``Level``, which holds the next one down, and so
on: "this structure is recursive to indicate the hierarchy of the levels".
The 3.x writer once wrote them as siblings, which the schema refuses past one
level (fixed in #422; pinned here). A node naming a level reads at that level
only to skip *down* from its depth; naming one above it, the chain cannot
hold.
"""

from collections.abc import Callable

import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support.factories import code_urn
from tests.support.unchecked import refusal
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_LEVELS = [Level(id=f"L{depth}", name={"en": f"level {depth}"}) for depth in range(3)]


def _node(code: str, *children: HierarchicalCode, level: Level | None = None) -> HierarchicalCode:
    return HierarchicalCode(id=code, code=Ref.unresolved(code_urn("CL", code)), level=level, children=list(children))


def _hierarchy(*tree: HierarchicalCode) -> Hierarchy:
    return Hierarchy(id="H", maintainer="AG", name={"en": "h"}, has_formal_levels=True, levels=_LEVELS, tree=list(tree))


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_three_levels_are_written_as_a_chain_and_read_back_in_order(version: SchemaVersion) -> None:
    hierarchy = _hierarchy(_node("A", _node("B", _node("C"))))
    data = serialize(StructureMessage(hierarchies=[hierarchy]), version=version)
    assert_valid_sdmx_ml(data, compile_structure_schema(version))

    (element,) = etree.fromstring(data).iterfind(".//{*}Hierarchy")
    chain = []
    holder = element
    while (level := holder.find("{*}Level")) is not None:
        assert len(holder.findall("{*}Level")) == 1, "one Level per holder: a chain, not siblings"
        chain.append(level.get("id"))
        holder = level
    assert chain == ["L0", "L1", "L2"]

    back = parse(data)
    assert isinstance(back, StructureMessage)
    assert [level.id for level in back.hierarchies[0].levels] == ["L0", "L1", "L2"]


def _issues(build: Callable[[], object]) -> list[str]:
    return [issue.message for issue in refusal(build)]


def test_a_node_at_its_levels_depth_is_valid() -> None:
    assert _issues(lambda: _hierarchy(_node("A", _node("B", level=_LEVELS[1]), level=_LEVELS[0]))) == []


def test_a_node_may_skip_down_a_level() -> None:
    """The case the schema's Level reference exists for."""
    assert _issues(lambda: _hierarchy(_node("A", _node("B", level=_LEVELS[2])))) == []


def test_a_node_above_its_depth_is_refused() -> None:
    (message,) = _issues(lambda: _hierarchy(_node("A", _node("B", level=_LEVELS[0]))))
    assert message.startswith("at depth 1, it names level 'L0' at depth 0")


def test_a_node_naming_a_level_the_hierarchy_lacks_is_refused() -> None:
    (message,) = _issues(lambda: _hierarchy(_node("A", level=Level(id="ELSEWHERE"))))
    assert message == "names level 'ELSEWHERE', which this hierarchy does not define"
