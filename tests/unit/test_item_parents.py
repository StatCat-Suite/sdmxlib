"""An item's ``parent_id`` names another item of its scheme, and no chain of them loops (#453).

A code pointing at a parent its codelist does not hold validated cleanly and
serialised, and `roots` / `children_of` then gave a forest with orphans; a
strict registry refused it (18,207 such codes in the bundle's CL_GEOGRAPHY).
A self-parent or a cycle passed the same way, and leaves no root at all.
"""

from collections.abc import Callable

import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.model.codelist import Code, Codelist, CodelistExtension
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support.unchecked import refusal

pytestmark = pytest.mark.unit


def _codelist(*codes: tuple[str, str | None], extensions: tuple[CodelistExtension, ...] = ()) -> Codelist:
    return Codelist(
        id="CL",
        maintainer="AG",
        name={"en": "cl"},
        codes=[Code(id=i, name={"en": i}, parent_id=p) for i, p in codes],
        extensions=extensions,
    )


def _messages(build: Callable[[], object]) -> list[tuple[str, str]]:
    return [(issue.path.split(".", 1)[1], issue.message) for issue in refusal(build)]


def test_a_forest_is_valid() -> None:
    assert _messages(lambda: _codelist(("A", None), ("B", "A"), ("C", "B"), ("D", None))) == []


def test_a_dangling_parent_is_refused() -> None:
    assert _messages(lambda: _codelist(("A", None), ("B", "Z"))) == [
        ("codes[B].parent_id", "parent_id 'Z' is not a code in this scheme")
    ]


def test_a_self_parent_is_refused() -> None:
    assert _messages(lambda: _codelist(("A", "A"))) == [("codes[A].parent_id", "parent_id 'A' is the code itself")]


@pytest.mark.parametrize(
    ("codes", "cycle"),
    [
        pytest.param((("A", "B"), ("B", "A")), "A -> B -> A", id="two"),
        pytest.param((("A", "C"), ("B", "A"), ("C", "B"), ("D", "A")), "A -> C -> B -> A", id="three-and-a-tail"),
    ],
)
def test_a_cycle_is_refused_once(codes: tuple[tuple[str, str | None], ...], cycle: str) -> None:
    """One issue per cycle, however many of its members a walk starts from, and none for a tail into it."""
    (finding,) = _messages(lambda: _codelist(*codes))
    assert finding[1] == f"parent_id forms a cycle: {cycle}"


def test_a_long_chain_is_walked_without_recursion() -> None:
    chain = [("C0", None), *((f"C{i}", f"C{i - 1}") for i in range(1, 100_000))]
    assert _messages(lambda: _codelist(*chain)) == []


def test_an_extending_codelist_may_name_a_parent_it_inherits() -> None:
    """Its parent may be a code of the codelist it extends: only self-parents and cycles are its own."""
    base = Codelist(id="BASE", maintainer="AG", name={"en": "b"}, codes=[Code(id="P", name={"en": "p"})])
    extension = CodelistExtension(codelist=Ref.to(base))
    assert _messages(lambda: _codelist(("A", "P"), extensions=(extension,))) == []
    assert _messages(lambda: _codelist(("A", "A"), extensions=(extension,))) == [
        ("codes[A].parent_id", "parent_id 'A' is the code itself")
    ]


def test_a_concept_parent_is_checked_too() -> None:
    def scheme() -> ConceptScheme:
        return ConceptScheme(
            id="CS", maintainer="AG", name={"en": "cs"}, concepts=[Concept(id="A", name={"en": "a"}, parent_id="Z")]
        )

    assert _messages(scheme) == [("concepts[A].parent_id", "parent_id 'Z' is not a concept in this scheme")]


def test_a_document_with_a_dangling_parent_is_refused_on_read() -> None:
    """Read-time validation applies the same rule: the codelist is refused, the rest read."""
    doc = etree.fromstring(serialize(StructureMessage(codelists=[_codelist(("A", None), ("B", "A"))]), version="3.0"))
    (parent,) = doc.iterfind(".//{*}Code[@id='B']/{*}Parent")
    parent.text = "Z"

    msg = parse(etree.tostring(doc), strict=False)

    assert isinstance(msg, StructureMessage)
    (refusal,) = msg.refused
    assert "parent_id 'Z' is not a code in this scheme" in refusal.reason
