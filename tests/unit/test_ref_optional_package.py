"""A `<Ref>` may omit `@package` / `@class`, and must still resolve — #240.

`ref_el_to_urn` required both and returned `None` without them. The caller then
stored `None`, so the reference vanished: no exception, no warning, and a
document that validates against the XSD. Every field affected is a `Ref`, so
the symptom is an artefact that simply has no concept, or no codelist.

**The schema makes them optional.** Across the vendored 2.1 schemas `package`
is optional on 97 ref types and required on 10, and 83 declare a ``fixed=``
value — XSD's way of saying "you may leave this out, the schema supplies it".
A producer that relies on that is emitting valid SDMX-ML.

The fix supplies the same values the schema would, from the artefact class the
containing field already expects — the witness `resolve_ref` uses as a
conformance guard. So it cannot contradict the guard: it fills in precisely
what the guard would have checked.

Tested per ref *kind* rather than by one example, because the failure is silent
and the surface is wide: item refs (`ConceptIdentity`) and maintainable refs
(`Enumeration`) reach different branches, and the attribute may be missing
singly or together.

Why it had not bitten: the checked-in ECB fixtures all emit `package`, and so
do the registries tested against. Luck, not design.
"""

from __future__ import annotations

import functools

import pytest
from lxml import etree

from sdmxlib.formats.sdmx_ml21 import reader as r21
from sdmxlib.model.ref import Ref
from tests.support.xsd import compile_structure_schema

pytestmark = pytest.mark.unit

_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
 xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
 xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
 <mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>
  <mes:Prepared>2020-01-01T00:00:00</mes:Prepared><mes:Sender id="s"/></mes:Header>
 <mes:Structures>
  <str:Codelists><str:Codelist agencyID="AG" id="CL" version="1.0"
      isExternalReference="false" isFinal="false">
    <com:Name xml:lang="en">cl</com:Name>
    <str:Code id="A"><com:Name xml:lang="en">a</com:Name></str:Code>
  </str:Codelist></str:Codelists>
  <str:Concepts><str:ConceptScheme agencyID="AG" id="CS" version="1.0"
      isExternalReference="false" isFinal="false">
    <com:Name xml:lang="en">cs</com:Name>
    <str:Concept id="CONTACT"><com:Name xml:lang="en">c</com:Name></str:Concept>
  </str:ConceptScheme></str:Concepts>
  <str:DataStructures><str:DataStructure agencyID="AG" id="DSD" version="1.0"
      isExternalReference="false" isFinal="false">
   <com:Name xml:lang="en">d</com:Name>
   <str:DataStructureComponents>
    <str:DimensionList id="DimensionDescriptor">
     <str:Dimension id="DIM" position="1">
      <str:ConceptIdentity><Ref agencyID="AG" maintainableParentID="CS"
        maintainableParentVersion="1.0" id="CONTACT" {concept_attrs}/></str:ConceptIdentity>
      <str:LocalRepresentation><str:Enumeration><Ref agencyID="AG" id="CL"
        version="1.0" {codelist_attrs}/></str:Enumeration></str:LocalRepresentation>
     </str:Dimension>
    </str:DimensionList>
    <str:MeasureList id="MeasureDescriptor">
     <str:PrimaryMeasure id="OBS_VALUE"><str:ConceptIdentity><Ref agencyID="AG"
       maintainableParentID="CS" maintainableParentVersion="1.0" id="CONTACT"
       class="Concept" package="conceptscheme"/></str:ConceptIdentity></str:PrimaryMeasure>
    </str:MeasureList>
   </str:DataStructureComponents></str:DataStructure></str:DataStructures>
 </mes:Structures></mes:Structure>"""

#: The four ways a producer may spell a `<Ref>`, all schema-valid. `class` is
#: `fixed` on the typed ref types just as `package` is, so a document may omit
#: either, both, or neither.
_SPELLINGS = [
    pytest.param('class="Concept" package="conceptscheme"', 'class="Codelist" package="codelist"', id="both"),
    pytest.param('class="Concept"', 'class="Codelist"', id="no-package"),
    pytest.param('package="conceptscheme"', 'package="codelist"', id="no-class"),
    pytest.param("", "", id="neither"),
]

_CONCEPT_URN = "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=AG:CS(1.0).CONTACT"
_CODELIST_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL(1.0)"


def _doc(concept_attrs: str, codelist_attrs: str) -> bytes:
    return _HEADER.format(concept_attrs=concept_attrs, codelist_attrs=codelist_attrs).encode()


@functools.cache
def _schema() -> etree.XMLSchema:
    return compile_structure_schema("2.1")


@pytest.mark.parametrize(("concept_attrs", "codelist_attrs"), _SPELLINGS)
def test_the_document_is_schema_valid_however_it_spells_the_ref(concept_attrs: str, codelist_attrs: str) -> None:
    """The premise. If the omitting forms were invalid there would be no bug.

    Asserted first and separately, because the whole argument for reading them
    is that the schema permits them — and the fixture claiming so is not the
    same as the XSD agreeing.
    """
    schema = _schema()
    assert schema.validate(etree.fromstring(_doc(concept_attrs, codelist_attrs))), str(schema.error_log)


@pytest.mark.parametrize(("concept_attrs", "codelist_attrs"), _SPELLINGS)
def test_an_item_ref_resolves_however_it_spells_the_ref(concept_attrs: str, codelist_attrs: str) -> None:
    """`ConceptIdentity` — an item ref, carrying `maintainableParentID`.

    The case that surfaced this: every attribute in a 2.1 MetadataStructure
    fixture came back with no concept, and the write boundary then refused the
    artefact for a reason unrelated to the actual defect.
    """
    dsd = r21.read(_doc(concept_attrs, codelist_attrs)).data_structures["DSD"]
    concept = dsd.dimensions["DIM"].concept
    assert concept is not None, "the reference was dropped"
    assert str(concept.urn) == _CONCEPT_URN


@pytest.mark.parametrize(("concept_attrs", "codelist_attrs"), _SPELLINGS)
def test_a_maintainable_ref_resolves_however_it_spells_the_ref(concept_attrs: str, codelist_attrs: str) -> None:
    """`Enumeration` — a maintainable ref, no `maintainableParentID`.

    A different branch of `ref_el_to_urn` and a different call site, so a fix
    that only threaded the witness through `resolve_ref` would leave this one
    broken and this file green.
    """
    dsd = r21.read(_doc(concept_attrs, codelist_attrs)).data_structures["DSD"]
    representation = dsd.dimensions["DIM"].representation
    assert isinstance(representation, Ref), "the reference was dropped"
    assert str(representation.urn) == _CODELIST_URN


def test_the_spellings_agree_with_each_other() -> None:
    """Not just non-None — the *same* URN, whichever way it was written.

    A fallback that supplied a plausible-but-different package would satisfy
    every assertion above and still put the artefact somewhere no lookup finds.
    """
    urns = set()
    for param in _SPELLINGS:
        concept_attrs, codelist_attrs = (str(v) for v in param.values)
        dsd = r21.read(_doc(concept_attrs, codelist_attrs)).data_structures["DSD"]
        concept = dsd.dimensions["DIM"].concept
        representation = dsd.dimensions["DIM"].representation
        assert concept is not None
        assert isinstance(representation, Ref)
        urns.add((str(concept.urn), str(representation.urn)))
    assert urns == {(_CONCEPT_URN, _CODELIST_URN)}, f"spellings disagreed: {urns}"


def test_a_ref_the_reader_cannot_type_still_needs_its_own_package() -> None:
    """The deliberate non-fix, kept visible.

    A heterogeneous ref — a categorisation source, a metadata target — passes
    `object` as the witness, which carries no package/class pair. Those must
    self-describe, because nothing else knows what they point at, so they keep
    the old behaviour rather than acquiring a wrong default.

    Written against the mechanism rather than a document, because the fields
    that pass `object` are exactly the ones with no fixed answer to assert.
    """
    from sdmxlib.formats.sdmx_ml21.reader import _helpers

    ref = etree.fromstring('<Ref agencyID="AG" id="X" class="Codelist"/>')
    assert _helpers.ref_el_to_urn(ref, object) is None, "object must not supply a package"
    from sdmxlib.model.codelist import Codelist

    assert _helpers.ref_el_to_urn(ref, Codelist) is not None, "a real witness must"
