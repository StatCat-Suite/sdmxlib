"""A reference that is not a `Ref` is refused, not written as NULL (#279).

`Dimension.concept` is declared `Ref[Concept] | None`, and attrs accepts a bare
`Concept` at runtime. The writer's `str(x.urn) if isinstance(x, Ref) else None`
then read as "no reference" and meant "no reference *or* something I did not
recognise" — so passing the item itself stored NULL. The artefact validated,
round-tripped, and had simply lost the edge; concept-driven discovery answered
`[]` against a catalogue that plainly used the concept.

Both checkers catch it in typed code. A notebook, a JSON-driven builder or any
untyped construction path is not checked, which is where it bites — and it bit
a fixture in this repo while writing the REST executor tests.

The distinction being restored is *absent* versus *malformed*. `None` is a real
answer and stays one; anything else is a mistake the store cannot represent.
"""

from __future__ import annotations

import re

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

AG = Agency(id="AG")


def _scheme() -> ConceptScheme:
    return ConceptScheme(
        id="CS", maintainer=AG, name={"en": "CS"}, concepts=[Concept(id="REF_AREA", name={"en": "Area"})]
    )


def _dsd(concept: object) -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer=AG,
        name={"en": "D"},
        dimensions=[Dimension(id="REF_AREA", concept=concept)],  # pyrefly: ignore[bad-argument-type]
    )


def test_a_bare_concept_is_refused() -> None:
    scheme = _scheme()
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(scheme)
        with pytest.raises(TypeError, match=re.escape("must be a Ref or None")):
            registry.add(_dsd(scheme.concepts["REF_AREA"]))


def test_the_refusal_says_how_to_build_the_ref() -> None:
    """The reason a caller passes the item is usually that they *have* it and
    the `Ref` looks like ceremony — and for an item it is genuinely not
    obvious, since the urn needs the parent scheme the component does not
    carry. A message that only says "wrong type" leaves them there.
    """
    scheme = _scheme()
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(scheme)
        with pytest.raises(TypeError) as caught:
            registry.add(_dsd(scheme.concepts["REF_AREA"]))
    message = str(caught.value)
    assert "Dimension.concept" in message
    assert "got Concept" in message
    assert "Ref[T].unresolved" in message
    assert "parent scheme" in message


def test_a_proper_ref_still_writes_and_round_trips() -> None:
    """The complement: the fix the message names must actually work."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(_scheme())
        registry.add(_dsd(concept_ref("REF_AREA", agency="AG", scheme_id="CS", version="1.0")))
        found = registry.query(sl.q.component).filter(sl.q.component.concept.id == "REF_AREA").count()
        assert found == 1, "the edge must survive the round-trip, which is what #279 lost"


def test_none_is_still_a_real_answer() -> None:
    """*Absent* and *malformed* were conflated; each is refused for what it is.

    Absent is refused as the DSD is built (#492) — a component with no concept,
    which no format can write (#419) — not as a bare item.
    """
    with pytest.raises(ValidationError, match="ConceptIdentity is required"):
        _dsd(None)


def test_a_bare_codelist_as_representation_is_wrapped_not_refused() -> None:
    """The distinction that decides which fields needed fixing.

    A converter can wrap a bare **maintainable**, whose urn is self-contained,
    and `representation` does exactly that. It cannot wrap a bare **item** — a
    concept or a code — because the urn needs the parent scheme the field does
    not carry. So the item-valued fields are the ones that reached the writer
    holding something it could not turn into a urn, and they are the ones this
    change is about.
    """
    codelist = Codelist(id="CL", maintainer=AG, name={"en": "CL"}, codes=[Code(id="A", name={"en": "a"})])
    dimension = Dimension(id="D1", concept=concept_ref("D1"), representation=codelist)
    assert isinstance(dimension.representation, Ref)

    with sl.LocalRegistry(":memory:") as registry:
        registry.add(codelist)
        registry.add(DataStructure(id="DSD", maintainer=AG, name={"en": "D"}, dimensions=[dimension]))
        assert registry.query(sl.q.component).filter(sl.q.component.codelist.id == "CL").count() == 1


def test_a_bare_code_in_a_hierarchy_is_refused() -> None:
    """`HierarchicalCode.code` is the second item-valued field, and its
    converter passes a bare `Code` straight through for the same reason."""
    codelist = Codelist(id="CL", maintainer=AG, name={"en": "CL"}, codes=[Code(id="A", name={"en": "a"})])
    node = HierarchicalCode(id="A", code=codelist.codes["A"])  # pyrefly: ignore[bad-argument-type]
    assert not isinstance(node.code, Ref), "the converter cannot build an item urn, which is the whole problem"

    hierarchy = Hierarchy(id="H", maintainer=AG, name={"en": "H"}, tree=[node])
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(codelist)
        with pytest.raises(TypeError, match=re.escape("HierarchicalCode.code must be a Ref")):
            registry.add(hierarchy)


def test_every_reference_read_in_the_writer_goes_through_the_check() -> None:
    """The audit the issue asked for, kept as a test.

    A new `str(x.urn) if isinstance(x, Ref) else None` is the exact spelling
    that lost the edge, so its reappearance should fail here rather than be
    found by a query returning nothing.
    """
    from pathlib import Path

    from tests.support import repo

    source = Path(repo.ROOT / "src" / "sdmxlib" / "storage" / "writers.py").read_text()
    assert "if isinstance(node.code, Ref) else None" not in source
    assert "if isinstance(concept_ref, Ref) else None" not in source
    assert "if isinstance(rm.source, Ref) else None" not in source
    assert "if isinstance(rm.target, Ref) else None" not in source


def test_a_ref_typed_field_accepts_an_unresolved_ref() -> None:
    """`Ref` covers resolved and unresolved alike, so the check must not
    accidentally require resolution."""
    unresolved = concept_ref("REF_AREA", agency="AG", scheme_id="CS", version="1.0")
    assert isinstance(unresolved, Ref)
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(_dsd(unresolved))
        assert registry.query(sl.q.dsd).count() == 1
