"""The 3.0 and 3.1 writers must emit the same information for one model.

`sdmx_ml31` was forked from `sdmx_ml30` "at parity" and drifted. The fixture
half of that drift is guarded by `test_fixture_coverage_parity`; this is the
code half, and it is the one that was actually losing data.

Measured before this existed, on one message: the 3.1 writer emitted **no
annotations at all** for codes, concepts, dimensions, time dimensions,
attributes, measures, agencies, data providers, hierarchical codes or metadata
attributes — ten `write_annotations` calls present in 3.0 and absent here — and
dropped `Concept/Parent`, so concept hierarchies came out flat. Every
wire-conformance test passed throughout, because emitting *less* is still
schema-valid: the XSD makes annotations optional, so nothing it can say
distinguishes "no annotations" from "annotations dropped".

What this guard cannot see is a field **both** writers drop. `CategoryScheme`
and `Category` annotations are exactly that — no writer emits them, 2.1
included — so the comparison is silent and #354 tracks it separately.

The comparison is deliberately on the emitted **vocabulary** — element and
attribute names — rather than on a byte diff. Namespaces differ, ordering may
differ, and neither is the question. What the question is: did anything the
model carries fail to reach the document.
"""

from __future__ import annotations

import re
from pathlib import Path

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_ml import _v3_writer as v3_writer
from sdmxlib.formats.sdmx_ml30 import writer as w30
from sdmxlib.formats.sdmx_ml31 import writer as w31
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.constraint import ConstraintAttachment, DataConstraint
from sdmxlib.model.hierarchy import HierarchyAssociation, Level
from sdmxlib.model.mapping import (
    ComponentMap,
    ConceptSchemeMap,
    ItemMap,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import (
    DataConsumer,
    DataConsumerScheme,
    DataProvider,
    DataProviderScheme,
    MetadataProvider,
    MetadataProviderScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

#: Elements 3.0 emits and 3.1 must not, with the reason the XSD gives. Each is
#: also an entry in `formats/gaps.py`, so a caller can ask before writing.
_REMOVED_IN_31 = {
    "ReleaseCalendar": "3.1 removed ReleaseCalendar from DataConstraintType",
    "Periodicity": "child of ReleaseCalendar",
    "Offset": "child of ReleaseCalendar",
    "Tolerance": "child of ReleaseCalendar",
}

#: Attributes, same rule.
_ATTRS_REMOVED_IN_31 = {
    "role": "3.1 DataConstraint is allowed-only; @role was removed",
    "version": "3.1 prohibits Categorisation/@version",
}


def _annotated(tag: str) -> list[sl.Annotation]:
    return [sl.Annotation(id=tag, type=tag, title=tag)]


def _item_ref[T](cls: type[T], *, package: str, scheme: str, item_id: str) -> Ref[T]:
    """A reference to a child item. `Ref.to` refuses these — only maintainables
    carry a URN of their own — so the scheme URN plus an item fragment."""
    return Ref[T].unresolved(
        SdmxUrn.make(
            cls,
            package=package,
            artefact_class=cls.__name__,
            agency="AG",
            id=scheme,
            version="1.0",
            item_id=item_id,
        )
    )


def _kitchen_sink() -> sl.StructureMessage:
    """One message reaching every `write_annotations` call site in the 3.0 writer.

    Annotations specifically, because they are the field the fork lost: they
    hang off nearly every element, they are optional in the XSD, and a writer
    that omits them produces a perfectly valid document.

    Every site gets its **own** marker, so a failure names the artefact rather
    than the fact that some annotation somewhere went missing. The first draft
    of this fixture annotated the items but not the schemes and skipped ten
    artefact kinds outright; planting each restored line back out one at a time
    showed 17 of 28 deletions surviving it. `test_every_annotation_site_is_reached`
    is what stops that recurring — it counts the call sites in the 3.0 writer
    and fails when one has no marker here.
    """
    codelist = sl.Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "c"},
        annotations=_annotated("CLANN"),
        codes=[sl.Code(id="X", name={"en": "x"}, annotations=_annotated("CODEANN"))],
    )
    concepts = sl.ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "cs"},
        annotations=_annotated("CSANN"),
        concepts=[
            sl.Concept(id="P", name={"en": "p"}),
            sl.Concept(id="C", name={"en": "c"}, parent_id="P", annotations=_annotated("CONCEPTANN")),
        ],
    )
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        annotations=_annotated("DSDANN"),
        dimensions=[
            sl.Dimension(
                id="F", representation=Ref.to(codelist), annotations=_annotated("DIMANN"), concept=concept_ref("F")
            )
        ],
        attributes=[
            sl.Attribute(
                attachment=sl.AttachmentLevel.DATASET,
                id="S",
                annotations=_annotated("ATTRANN"),
                concept=concept_ref("S"),
            )
        ],
        measures=[sl.Measure(id="OBS_VALUE", annotations=_annotated("MEASANN"), concept=concept_ref("OBS_VALUE"))],
        metadata_attributes=[
            sl.MetadataAttributeUsage(attachment=sl.AttachmentLevel.DATASET, id="MA", annotations=_annotated("MAUANN"))
        ],
        groups=[sl.Group(id="G", dimension_ids=["F"], annotations=_annotated("GROUPANN"))],
        dimension_descriptor_annotations=_annotated("DIMLISTANN"),
        attribute_descriptor_annotations=_annotated("ATTRLISTANN"),
        measure_descriptor_annotations=_annotated("MEASLISTANN"),
        time_dimension=sl.TimeDimension(annotations=_annotated("TDANN"), concept=concept_ref("X")),
    )
    dataflow = sl.Dataflow(
        id="DF",
        maintainer="AG",
        version="1.0",
        name={"en": "df"},
        annotations=_annotated("DFANN"),
        structure=Ref.to(dsd),
    )
    category_scheme = CategoryScheme(
        id="CAT_SCHEME",
        maintainer="AG",
        version="1.0",
        name={"en": "cats"},
        categories=[Category(id="TOP", name={"en": "top"})],
    )
    categorisation = Categorisation(
        id="CATN",
        maintainer="AG",
        version="1.0",
        name={"en": "catn"},
        annotations=_annotated("CATANN"),
        source=Ref.to(dataflow),
        target=Ref.to(category_scheme),
    )
    providers = DataProviderScheme(
        id="DATA_PROVIDERS",
        maintainer="AG",
        version="1.0",
        name={"en": "p"},
        annotations=_annotated("DPSCHEMEANN"),
        providers=[DataProvider(id="P1", name={"en": "p1"}, annotations=_annotated("PROVIDERANN"))],
    )
    metadata_providers = MetadataProviderScheme(
        id="METADATA_PROVIDERS",
        maintainer="AG",
        version="1.0",
        name={"en": "mp"},
        annotations=_annotated("MDPSCHEMEANN"),
        providers=[MetadataProvider(id="MP1", name={"en": "mp1"}, annotations=_annotated("MDPROVIDERANN"))],
    )
    # Reaches both sites `_write_organisation_scheme` and `_write_organisation`
    # share with an organisation unit scheme (#469).
    consumers = DataConsumerScheme(
        id="DATA_CONSUMERS",
        maintainer="AG",
        version="1.0",
        name={"en": "c"},
        annotations=_annotated("DCSCHEMEANN"),
        consumers=[DataConsumer(id="C1", name={"en": "c1"}, annotations=_annotated("CONSUMERANN"))],
    )
    provision = ProvisionAgreement(
        id="PA",
        maintainer="AG",
        version="1.0",
        name={"en": "pa"},
        annotations=_annotated("PAANN"),
        dataflow=Ref.to(dataflow),
        data_provider=_item_ref(DataProvider, package="base", scheme="DATA_PROVIDERS", item_id="P1"),
    )
    concept_scheme_map = ConceptSchemeMap(
        id="CSM",
        maintainer="AG",
        version="1.0",
        name={"en": "csm"},
        annotations=_annotated("CSMAPANN"),
        source=Ref.to(concepts),
        target=Ref.to(concepts),
        maps=(ItemMap(source_id="P", target_id="C", annotations=_annotated("ITEMMAPANN")),),
    )
    representation_map = RepresentationMap(
        id="RM",
        maintainer="AG",
        version="1.0",
        name={"en": "rm"},
        annotations=_annotated("RMAPANN"),
        sources=(Ref.to(codelist),),
        targets=(Ref.to(codelist),),
        mappings=(RepresentationMapping(sources=("X",), targets=("X",), annotations=_annotated("VALUEMAPANN")),),
    )
    structure_map = StructureMap(
        id="SM",
        maintainer="AG",
        version="1.0",
        name={"en": "sm"},
        annotations=_annotated("SMAPANN"),
        source=Ref.to(dsd),
        target=Ref.to(dsd),
        maps=(ComponentMap(sources=("F",), targets=("F",), annotations=_annotated("COMPMAPANN")),),
        epoch_maps=(
            sl.EpochMap(
                id="E",
                component_maps=[("F", "F")],
                base_period="1970",
                target_frequency_id="A",
                annotations=_annotated("DATEMAPANN"),
            ),
        ),
        frequency_format_mappings=(
            sl.FrequencyFormatMapping(id="FF", frequency_id="A", date_pattern="yyyy", annotations=_annotated("FFMANN")),
        ),
        fixed_value_maps=(sl.FixedValueMap(source_id="F", values=("X",), annotations=_annotated("FIXEDANN")),),
    )
    constraint = DataConstraint(
        id="CC",
        maintainer="AG",
        version="1.0",
        name={"en": "cc"},
        annotations=_annotated("CCANN"),
        attachment=ConstraintAttachment(dataflows=(Ref.to(dataflow),)),
        regions=[sl.CubeRegion(annotations=_annotated("REGIONANN"))],
    )
    hierarchy = sl.Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "h"},
        annotations=_annotated("HIERANN"),
        has_formal_levels=True,
        levels=[Level(id="L1", name={"en": "l1"}, annotations=_annotated("LEVELANN"))],
        tree=[
            sl.HierarchicalCode(
                id="N",
                annotations=_annotated("HCODEANN"),
                code=_item_ref(sl.Code, package="codelist", scheme="CL", item_id="X"),
            )
        ],
    )
    hierarchy_association = HierarchyAssociation(
        id="HA",
        maintainer="AG",
        version="1.0",
        name={"en": "ha"},
        annotations=_annotated("HAANN"),
        linked_hierarchy=Ref.to(hierarchy),
        linked_object=Ref.to(dsd),
    )
    agencies = sl.AgencyScheme(
        id="AGENCIES",
        maintainer="AG",
        version="1.0",
        name={"en": "a"},
        annotations=_annotated("AGSCHEMEANN"),
        agencies=[sl.Agency(id="A1", name={"en": "a1"}, annotations=_annotated("AGENCYANN"))],
    )
    msd = MetadataStructure(
        id="MSD",
        maintainer="AG",
        version="1.0",
        name={"en": "m"},
        annotations=_annotated("MSDANN"),
        attributes=[MetadataAttribute(id="MA", annotations=_annotated("METAATTRANN"), concept=concept_ref("MA"))],
        attribute_list_annotations=_annotated("MALISTANN"),
    )
    # Built before the provision agreement, which references it. It used to
    # come after, so the agreement pointed its `metadataflow` at the *MSD* —
    # a fixture bug nothing caught while `tests/` was unchecked (#347).
    metadataflow = Metadataflow(
        id="MDF",
        maintainer="AG",
        version="1.0",
        name={"en": "mdf"},
        annotations=_annotated("MDFANN"),
        structure=Ref.to(msd),
    )
    metadata_provision = MetadataProvisionAgreement(
        id="MPA",
        maintainer="AG",
        version="1.0",
        name={"en": "mpa"},
        annotations=_annotated("MPAANN"),
        metadataflow=Ref.to(metadataflow),
    )
    value_list = sl.ValueList(
        id="VL",
        maintainer="AG",
        version="1.0",
        name={"en": "v"},
        annotations=_annotated("VLANN"),
        items=[sl.ValueItem(id="kg", annotations=_annotated("VALUEANN"))],
    )
    return sl.StructureMessage(
        codelists=[codelist],
        value_lists=[value_list],
        concept_schemes=[concepts],
        data_structures=[dsd],
        dataflows=[dataflow],
        category_schemes=[category_scheme],
        categorisations=[categorisation],
        provision_agreements=[provision],
        concept_scheme_maps=[concept_scheme_map],
        representation_maps=[representation_map],
        structure_maps=[structure_map],
        constraints=[constraint],
        hierarchies=[hierarchy],
        hierarchy_associations=[hierarchy_association],
        agency_schemes=[agencies],
        data_provider_schemes=[providers],
        metadata_provider_schemes=[metadata_providers],
        data_consumer_schemes=[consumers],
        metadata_structures=[msd],
        metadataflows=[metadataflow],
        metadata_provision_agreements=[metadata_provision],
    )


#: Every marker in `_kitchen_sink`, one per `write_annotations` call site in the
#: 3.0 writer. `test_every_annotation_site_is_reached` ties the two together.
_MARKERS = [
    "CLANN",
    "CODEANN",
    "VLANN",
    "VALUEANN",
    "CSANN",
    "CONCEPTANN",
    "DSDANN",
    "DIMANN",
    "TDANN",
    "ATTRANN",
    "MEASANN",
    "MAUANN",
    "GROUPANN",
    "DIMLISTANN",
    "ATTRLISTANN",
    "MEASLISTANN",
    "DFANN",
    "CATANN",
    "PAANN",
    "CSMAPANN",
    "ITEMMAPANN",
    "RMAPANN",
    "VALUEMAPANN",
    "SMAPANN",
    "COMPMAPANN",
    "DATEMAPANN",
    "FFMANN",
    "FIXEDANN",
    "CCANN",
    "REGIONANN",
    "AGSCHEMEANN",
    "AGENCYANN",
    "DPSCHEMEANN",
    "PROVIDERANN",
    "MDPSCHEMEANN",
    "MDPROVIDERANN",
    "DCSCHEMEANN",
    "CONSUMERANN",
    "HIERANN",
    "LEVELANN",
    "HCODEANN",
    "HAANN",
    "MSDANN",
    "METAATTRANN",
    "MALISTANN",
    "MDFANN",
    "MPAANN",
]

#: Annotation sites only 3.1 has, kept out of `_kitchen_sink` so the parity
#: checks above stay about parity. An AvailabilityConstraint's (#421).
_MARKERS_31_ONLY = ["AVAILANN"]


def _vocabulary(blob: bytes) -> tuple[set[str], set[str]]:
    """Element local-names and attribute names in a document."""
    text = blob.decode()
    elements = {m.group(1) for m in re.finditer(r"<(?:\w+:)?(\w+)", text)}
    attributes = {m.group(1) for m in re.finditer(r"\s(\w+)=", text)}
    return elements, attributes


@pytest.fixture(scope="module")
def emitted() -> tuple[tuple[set[str], set[str]], tuple[set[str], set[str]]]:
    msg = _kitchen_sink()
    return _vocabulary(w30.write(msg)), _vocabulary(w31.write(msg))


def test_the_fixture_is_rich_enough_to_discriminate(emitted) -> None:
    """A message that exercised little would make both writers look alike."""
    (elements, _), _ = emitted
    assert len(elements) > 25, f"only {len(elements)} element kinds emitted; the fixture is too thin"
    assert "Annotation" in elements, "annotations are the field the fork lost — they must be exercised"


def test_the_3_1_writer_emits_every_element_the_3_0_writer_does(emitted) -> None:
    """The drift, as the property it broke."""
    (elements_30, _), (elements_31, _) = emitted
    missing = sorted(elements_30 - elements_31 - set(_REMOVED_IN_31))
    assert not missing, (
        f"the 3.1 writer drops these elements for a model 3.0 writes in full: {missing}. "
        "Emit them, or list them in _REMOVED_IN_31 with the XSD reason and add a gaps.py entry."
    )


def test_the_3_1_writer_emits_every_attribute_the_3_0_writer_does(emitted) -> None:
    (_, attrs_30), (_, attrs_31) = emitted
    missing = sorted(attrs_30 - attrs_31 - set(_ATTRS_REMOVED_IN_31))
    assert not missing, f"the 3.1 writer drops these attributes: {missing}"


def test_the_3_1_writer_adds_nothing_the_3_0_writer_lacks(emitted) -> None:
    """The reverse. 3.1 adding an element is fine in principle and worth
    noticing, since at parity there is nothing it should have that 3.0 lacks."""
    (elements_30, _), (elements_31, _) = emitted
    extra = sorted(elements_31 - elements_30)
    assert not extra, f"the 3.1 writer emits elements 3.0 does not: {extra}"


@pytest.mark.parametrize("marker", _MARKERS)
def test_each_annotation_reaches_both_documents(marker: str) -> None:
    """Named one by one, so a failure says *which* artefact lost its notes.

    The vocabulary check above would pass if a single `Annotation` element
    survived anywhere — these are what make it specific.
    """
    msg = _kitchen_sink()
    # `id="CODEANN"`, not a bare substring search: `"CODEANN" in "HCODEANN"` is
    # True, so the plain form passed for a code whose annotation was dropped as
    # long as the *hierarchical* code kept its own. Found by planting exactly
    # that deletion and watching this test stay green.
    needle = f'id="{marker}"'
    assert needle in w30.write(msg).decode(), f"3.0 lost {marker} — fixture problem, not a 3.1 one"
    assert needle in w31.write(msg).decode(), f"the 3.1 writer dropped {marker}"


def test_concept_parents_survive_both() -> None:
    """Not an annotation, and lost the same way: `Concept/Parent` was absent
    from the 3.1 writer, so a concept hierarchy came out flat."""
    msg = _kitchen_sink()
    assert "<str:Parent>P" in w30.write(msg).decode()
    assert "<str:Parent>P" in w31.write(msg).decode()


def test_every_annotation_site_is_reached() -> None:
    """A new annotated element in the 3.0 writer must gain a marker here.

    Without this the fixture silently rots: the parametrised test above only
    checks what it was given, so a writer growing a 28th `write_annotations`
    call would be guarded by nothing and nobody would know. Counting the call
    sites is crude — it cannot tell *which* site is unreached — but it is the
    thing that fails, and the failure names the file to read.
    """
    # The **shared** writer since #346: `sdmx_ml30/writer.py` is now 49 lines
    # binding `V3Writer`, so reading it counts zero call sites and this guard
    # passed while guarding nothing. Both generations write through the same
    # module now, which is also why one count covers both.
    source = Path(v3_writer.__file__).read_text(encoding="utf-8")
    sites = source.count("_helpers.write_annotations(")
    markers = len(_MARKERS) + len(_MARKERS_31_ONLY)
    assert sites == markers, (
        f"the v3 writer has {sites} write_annotations call sites but the fixtures "
        f"carry {markers} markers. Annotate the new artefact in the fixture "
        "and add its marker, or this drift guard is guarding one element less."
    )


def test_an_availability_constraint_is_annotated_in_3_1_alone() -> None:
    """3.1's only annotation site 3.0 lacks: 3.0 leaves the constraint out (#421)."""
    (flow,) = _kitchen_sink().dataflows
    availability = sl.AvailabilityConstraint(
        attachment=ConstraintAttachment(dataflows=(Ref.to(flow),)),
        region=sl.CubeRegion(),
        annotations=_annotated("AVAILANN"),
    )
    msg = attrs.evolve(_kitchen_sink(), availability_constraints=[availability])
    (marker,) = _MARKERS_31_ONLY
    assert f'id="{marker}"' in w31.write(msg).decode()
    assert f'id="{marker}"' not in w30.write(msg).decode()
