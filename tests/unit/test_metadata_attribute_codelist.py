"""An enumerated metadata attribute uses a Codelist, and its occurrences are a range (GH-28).

IM §7.3.4.1 p. 98, of a MetadataAttribute: "If the Representation is
enumerated, then is must use a Codelist", and it is "mandatory (minOccurs=1
or more) or optional (minOccurs=0)". The 3.0 and 3.1 XSDs and the SDMX-JSON
2.0 schema type the enumeration a codelist reference. The model took a
`ValueList` and any occurrence bounds.
"""

from typing import cast

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.formats import parse, serialize, to_json
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure, codelist_representation
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_CODELIST = sl.Codelist(id="CL_A", maintainer="AG", name={"en": "a"}, codes=[sl.Code(id="X", name={"en": "x"})])
_VALUE_LIST = sl.ValueList(id="VL_A", maintainer="AG", name={"en": "a"})


def _msd(*attributes: MetadataAttribute) -> MetadataStructure:
    return MetadataStructure(id="MSD", maintainer="AG", name={"en": "msd"}, attributes=list(attributes))


def test_a_codelist_enumerates_a_metadata_attribute() -> None:
    """The discrimination check: what the refusals below refuse is the value list alone."""
    attribute = MetadataAttribute(id="A", representation=_CODELIST)
    assert isinstance(attribute.representation, Ref)
    assert attribute.representation.urn == _CODELIST.urn
    assert isinstance(MetadataAttribute(id="B", representation=_CODELIST.urn).representation, Ref)


@pytest.mark.parametrize("value", [_VALUE_LIST, Ref.to(_VALUE_LIST), _VALUE_LIST.urn], ids=["artefact", "ref", "urn"])
def test_a_value_list_is_refused_from_an_untyped_caller(value: object) -> None:
    """The types refuse one; the converter refuses what an untyped caller passes,
    which the `cast` stands in for."""
    with pytest.raises(sl.ValidationError, match=r"uses a Codelist \(IM §7\.3\.4\.1\)"):
        MetadataAttribute(id="A", representation=cast("sl.Codelist", value))
    attribute = MetadataAttribute(id="A", representation=_CODELIST)
    with pytest.raises(sl.ValidationError, match="is a ValueList"):
        attrs.evolve(attribute, representation=cast("sl.Codelist", value))


def test_a_value_list_taken_from_the_concept_is_refused() -> None:
    scheme = ConceptScheme(
        id="CS",
        maintainer="AG",
        name={"en": "cs"},
        concepts=[Concept(id="C", name={"en": "c"}, core_representation=Ref.to(_VALUE_LIST))],
    )
    with pytest.raises(sl.ValidationError, match="is a ValueList"):
        MetadataAttribute(id="C", concept=scheme.ref_to("C"))


def test_the_readers_refuse_a_value_list_through_one_helper() -> None:
    with pytest.raises(sl.ValidationError, match="is a ValueList"):
        codelist_representation(Ref.to(_VALUE_LIST))
    assert codelist_representation(Ref.to(_CODELIST)) == Ref.to(_CODELIST)


@pytest.mark.parametrize(
    ("write", "urns"),
    [(lambda m: serialize(m, version="3.0"), 1), (to_json, 2)],
    ids=["sdmx-ml-3.0", "sdmx-json"],
)
def test_a_document_naming_a_value_list_refuses_the_structure(write, urns: int) -> None:
    """A document whose metadata attribute names a value list: the reader refuses that
    MSD alone and reads the rest."""
    msd = _msd(MetadataAttribute(id="A", concept=concept_ref("A"), representation=_CODELIST))
    written: bytes = write(sl.StructureMessage(codelists=[_CODELIST], metadata_structures=[msd]))
    codelist_urn = str(_CODELIST.urn).encode()
    # SDMX-JSON also writes the codelist's own URN; the attribute's enumeration comes last.
    assert written.count(codelist_urn) == urns
    before, _, after = written.rpartition(codelist_urn)
    msg = parse(before + str(_VALUE_LIST.urn).encode() + after, strict=False)
    assert isinstance(msg, sl.StructureMessage)
    assert [r.artefact_class for r in msg.refused] == ["MetadataStructure"]
    assert len(msg.codelists) == 1


@pytest.mark.parametrize(("low", "high"), [(-1, 1), (0, 0), (2, 1)], ids=["negative-min", "zero-max", "max-below-min"])
def test_occurrences_that_are_no_range_are_refused(low: int, high: int) -> None:
    with pytest.raises(sl.ValidationError, match=r"attributes\[A\]\.max_occurs — occurs .* is not a range"):
        _msd(MetadataAttribute(id="A", concept=concept_ref("A"), min_occurs=low, max_occurs=high))


def test_occurrences_are_checked_on_a_nested_attribute() -> None:
    child = MetadataAttribute(id="B", concept=concept_ref("B"), min_occurs=3, max_occurs=2)
    with pytest.raises(sl.ValidationError, match=r"attributes\[A\]\.children\[B\]\.max_occurs"):
        _msd(MetadataAttribute(id="A", concept=concept_ref("A"), children=[child]))


@pytest.mark.parametrize(("low", "high"), [(0, 1), (1, None), (2, 5)], ids=["optional", "unbounded", "bounded"])
def test_a_range_is_accepted(low: int, high: int | None) -> None:
    msd = _msd(MetadataAttribute(id="A", concept=concept_ref("A"), min_occurs=low, max_occurs=high))
    assert msd.attributes["A"].min_occurs == low
