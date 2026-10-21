"""A `Ref` finds its target through the registry that bound it (#492).

Nothing writes to a `Ref` or to the artefact holding it. A registry's `add`
stores a copy whose references it binds, and the paths that read straight into
a registry bind as they build, so `add` has nothing to copy: a 499,138-node
hierarchy took 3.1 s to `add` as a copy, 1.3 s bound at build.
"""

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml30.writer import write as write30
from sdmxlib.model.ref import UnresolvedRef
from tests.support.factories import concept_ref
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_CL = sl.Codelist.urn_for(agency="T", id="CL_FREQ", version="1.0")


def _codelist(*codes: str) -> sl.Codelist:
    return sl.Codelist(
        id="CL_FREQ", maintainer="T", name={"en": "f"}, codes=[sl.Code(id=c, name={"en": c}) for c in codes]
    )


def _dsd() -> sl.DataStructure:
    return sl.DataStructure(
        id="DSD",
        maintainer="T",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=sl.Ref.unresolved(_CL))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _concepts() -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS",
        maintainer="T",
        name={"en": "c"},
        concepts=[sl.Concept(id="FREQ", name={"en": "f"}), sl.Concept(id="OBS_VALUE", name={"en": "v"})],
    )


def _document() -> bytes:
    return write30(
        sl.StructureMessage(codelists=[_codelist("A")], concept_schemes=[_concepts()], data_structures=[_dsd()])
    )


def test_parsing_into_a_registry_copies_nothing():
    """`parse(registry=…)` binds as it builds, so the registry holds the very objects returned."""
    reg = sl.InMemoryRegistry()
    msg = as_structure(parse(_document(), registry=reg))

    artefacts = list(msg.all_artefacts())
    assert len(artefacts) == 3
    for artefact in artefacts:
        assert reg.get(artefact.urn) is artefact, f"{artefact.urn} was copied on add"
    representation = msg.data_structures["DSD"].dimensions["FREQ"].representation
    assert isinstance(representation, sl.Ref)
    assert representation() is reg.get(_CL)


def test_parsing_without_a_registry_binds_to_the_document():
    msg = as_structure(parse(_document()))
    representation = msg.data_structures["DSD"].dimensions["FREQ"].representation
    assert isinstance(representation, sl.Ref)
    assert representation() is msg.codelists["CL_FREQ"]


def test_to_registry_binds_every_reference_to_the_new_registry():
    with sl.LocalRegistry(":memory:") as local:
        local.add(_concepts())
        local.add(_codelist("A"))
        local.add(_dsd())
        reg = local.to_registry()
    dsd = reg.get(_dsd().urn)
    assert dsd is not None
    representation = dsd.dimensions["FREQ"].representation
    assert isinstance(representation, sl.Ref)
    assert representation() is reg.get(_CL)
    assert dsd.dimensions["FREQ"].concept is not None
    assert dsd.dimensions["FREQ"].concept().id == "FREQ"


def test_a_reference_into_a_closed_store_says_why():
    with sl.LocalRegistry(":memory:") as local:
        local.add(_concepts())
        local.add(_codelist("A"))
        local.add(_dsd())
        dsd = local.get(_dsd().urn)
        assert dsd is not None
        representation = dsd.dimensions["FREQ"].representation
        assert isinstance(representation, sl.Ref)
        assert representation.resolved
    assert not representation.resolved
    with pytest.raises(UnresolvedRef, match=r"LocalRegistry\(.*:memory:.*, closed\) is closed"):
        representation()


def test_a_component_s_codelist_follows_a_replaced_codelist():
    """No cached enumeration (#119's) to go stale: a fuller codelist is what the next read finds."""
    reg = sl.InMemoryRegistry()
    reg.add(_concepts())
    reg.add(_dsd())
    reg.add(_codelist("A"))
    dimension = sl.DataStructure.urn_for(agency="T", id="DSD", version="1.0")
    stored = reg.get(dimension)
    assert stored is not None
    freq = stored.dimensions["FREQ"]
    assert freq.codelist.code_ids() == ["A"]

    reg.add(_codelist("A", "Q", "M"))

    assert freq.codelist.code_ids() == ["A", "Q", "M"]
