"""One `StructureMessage` with every field of every artefact populated.

The round-trip guards are only ever as good as what the fixture sets. A field
left `None` makes every check over it vacuous — the writer can drop it and the
comparison still passes, because there was nothing to lose. That is not a
hypothetical: `test_roundtrip_property_guard` has run since #159 with
hand-written `Check`s over six of the twenty artefact slots, and the ten
uncovered slots is where #345, #346 and #354 all came from.

So this fixture is *maximal* by construction and by test. Every attrs field on
every artefact class reachable from the message carries a value, and
`test_field_coverage.py::test_the_fixture_populates_every_field` walks the graph
and fails when one does not. Adding a field to the model without populating it
here fails there, rather than quietly widening the blind spot.

**Values are distinctive, not realistic.** `"uri-codelist"` rather than a
plausible URL, because a round-trip diff reports the value it lost and a
distinctive one says which field it came from. Where the SDMX grammar
constrains the value — ids, versions, URNs — the constraint wins.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import attrs

from sdmxlib.model.annotations import Annotation, Annotations, AnnotationURL
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    GeoFeatureSetCode,
    GeographicCodelist,
    GeoGridCode,
    GeoGridCodelist,
    InclusionMode,
    MemberValue,
)
from sdmxlib.model.concept import Concept, ConceptScheme, ISOConceptReference
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    DataKey,
    DataKeySet,
    KeyValue,
    ReferencePeriod,
    RegionValue,
    ReleaseCalendar,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    AttributeUsage,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MeasureDimension,
    MetadataAttributeUsage,
    TimeDimension,
)
from sdmxlib.model.header import Header, Party, Sender
from sdmxlib.model.hierarchy import HierarchicalCode, HierarchicalCodelist, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.link import Link
from sdmxlib.model.mapping import (
    BUILT_VALUE_MAP_TYPE,
    ComponentMap,
    ConceptSchemeMap,
    DatePatternMap,
    EpochMap,
    EpochPeriod,
    FixedValueMap,
    FrequencyFormatMapping,
    ItemMap,
    MapDataType,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    ResolvePeriod,
    StructureMap,
    StructureSet,
    ToValueType,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import (
    MetadataAttribute,
    MetadataStructure,
    MetadataTarget,
    ReportStructure,
    TargetObject,
)
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    Contact,
    DataConsumer,
    DataConsumerScheme,
    DataProvider,
    DataProviderScheme,
    MetadataProvider,
    MetadataProviderScheme,
    OrganisationUnit,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet, SentinelValue
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueItem, ValueList

AGENCY = "AG"
#: The metadata provider scheme's agency; see `metadata_providers` below.
METADATA_AGENCY = "AG_MD"
VERSION = "1.0"

#: Both languages on every `InternationalString`. A writer emitting only the
#: first locale is a real defect and a single-entry map cannot see it.
LANGS = ("en", "fr")


def _text(marker: str) -> dict[str, str]:
    return {lang: f"{marker}-{lang}" for lang in LANGS}


def _annotations(marker: str) -> Annotations:
    """Every field of `Annotation` set — including `text`, which is itself an
    `InternationalString` and so has its own per-locale drop to lose."""
    return Annotations(
        [
            Annotation(
                id=f"{marker}-ANN",
                type=f"{marker}-TYPE",
                title=f"{marker}-TITLE",
                # Several, one localised (#422): 2.1 keeps only the first.
                # The second carries SDMX-JSON's URN and type, which no
                # SDMX-ML version writes.
                urls=[
                    AnnotationURL(f"https://example.org/{marker}"),
                    AnnotationURL(
                        f"https://example.org/{marker}/fr",
                        lang="fr",
                        urn="urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_ANNOTATED(1.0)",
                        type="PDF",
                    ),
                ],
                text=_text(f"{marker}-TEXT"),
                value=f"{marker}-VALUE",
            )
        ]
    )


def _item_ref[T](cls: type[T], *, package: str, scheme: str, item_id: str) -> Ref[T]:
    """A reference to a child item — the scheme URN plus an item fragment.

    `Ref.to` refuses these: only maintainables carry a URN of their own.
    """
    return Ref[T].unresolved(
        SdmxUrn.make(
            cls,
            package=package,
            artefact_class=cls.__name__,
            agency=AGENCY,
            id=scheme,
            version=VERSION,
            item_id=item_id,
        )
    )


def _concept_ref(item_id: str) -> Ref[Concept]:
    return _item_ref(Concept, package="conceptscheme", scheme="CS", item_id=item_id)


def _maintainable(marker: str, **kwargs: Any) -> dict[str, Any]:
    """The `MaintainableArtefact` fields every artefact below shares.

    `Any` rather than `object`, and deliberately (#347). The result is splatted
    into twenty different constructors — `Codelist(**_maintainable_21(...))` — so
    every value lands in a differently-typed parameter. Declared `object`, the
    splat erased all of them and basedpyright reported 250 argument mismatches
    in this one file, none of them real: the values *are* the right types, the
    return annotation just refused to say so.

    A `TypedDict` cannot express it either, because `**kwargs` carries a
    different set of extra keys per call site. So this is the "one annotated
    boundary where the `Any` dies" case CLAUDE.md describes, and it is confined
    to this helper's signature rather than riding out into the fixture.
    """
    return {
        "id": marker,
        "maintainer": AGENCY,
        "version": VERSION,
        "name": _text(f"{marker}-NAME"),
        "description": _text(f"{marker}-DESC"),
        "annotations": _annotations(marker),
        "valid_from": datetime(2020, 1, 1),
        "valid_to": datetime(2030, 12, 31),
        "uri": f"https://example.org/uri/{marker}",
        "links": _links(marker),
        # Every one a stub, by the fixture's rule of setting each field off its
        # default — nothing checks a stub's content against its URLs (#422).
        "is_external_reference": True,
        "service_url": f"https://example.org/registry/{marker}",
        "structure_url": f"https://example.org/structure/{marker}.xml",
        "is_partial_language": True,
        **kwargs,
    }


#: What a 2.1 reader gives a map in `structure_set` beside the id `member_id`
#: composes (#421): the set's external reference, as its validity already is.
_IN_SS = {"service_url": "https://example.org/registry/SS", "structure_url": "https://example.org/structure/SS.xml"}


def _links(marker: str) -> tuple[Link, ...]:
    """Two links, every field of the first set (#422) — its language SDMX-JSON's alone.

    Neither is a ``self`` or ``describedby`` link, which SDMX-JSON spells as
    the identity and the uri; and the URN is an SDMX one, as its schema asks.
    """
    return (
        Link(
            rel="metadata",
            url=f"https://example.org/link/{marker}",
            urn="urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_LINKED(1.0)",
            type="PDF",
            hreflang="en",
        ),
        Link(rel="alternate", url=f"https://example.org/alternate/{marker}"),
    )


def _without_links(fields: dict[str, Any]) -> dict[str, Any]:
    """`fields` for one of 2.1's own containers, which SDMX 3.x never gave a `<com:Link>`."""
    return {key: value for key, value in fields.items() if key != "links"}


def _maintainable_21(marker: str, **kwargs: Any) -> dict[str, Any]:
    """`_maintainable`, plus the `legacy_is_final` 2.1 lets this class carry.

    `True`, which disagrees with `VERSION` (`1.0` is never final, #420), so
    the field holds something rather than the agreeing `None` a reader keeps.
    Only for the classes SDMX 2.1 gives an `isFinal`; the rest have no field.
    """
    return _maintainable(marker, legacy_is_final=True, **kwargs)


def _facet() -> Facet:
    """Every facet field. `type` is `Integer` so the numeric bounds are legal."""
    return Facet(
        type="Integer",
        max_length=10,
        min_length=1,
        max_value=99.0,
        min_value=1.0,
        decimals=2,
        pattern="[0-9]+",
        start_value=1.0,
        end_value=99.0,
        is_sequence=True,
        is_multilingual=True,
        interval=2.0,
        time_interval="P1D",
        start_time="2020-01-01",
        end_time="2030-12-31",
    )


def _enumeration_facet() -> Facet:
    """An `enumeration_format`, which every format types `CodedTextFormatType`.

    `_facet()` without `decimals` and `is_multilingual`: codes are strings, no
    format can carry either there, and `Registry.add` refuses them (#419) —
    so this is maximal within the grammar, like `_coding_facet()`.
    """
    return attrs.evolve(_facet(), decimals=None, is_multilingual=False)


def _sentinels(marker: str) -> tuple[SentinelValue, ...]:
    """A text format's sentinel values (#422) — never an enumeration or coding format's."""
    return (
        SentinelValue(value="-1", name=_text(f"{marker}-SENTINEL-NAME"), description=_text(f"{marker}-SENTINEL-DESC")),
        SentinelValue(value="NA", name=_text(f"{marker}-NA-NAME")),
    )


def _coding_facet() -> Facet:
    """A `Level.coding_format`, which the XSD types `CodingTextFormatType`.

    That is a *restriction* of the ordinary facet type: `textType` narrows to
    Alpha/AlphaNumeric/Numeric, the numeric bounds narrow to
    `xs:positiveInteger`, and `decimals`, `isMultiLingual` and the three time
    facets are absent in every format. So this cannot just be `_facet()` — a
    maximal fixture has to be maximal *within the grammar*, and `Registry.add`
    refuses what is outside it (#419).
    """
    return Facet(
        type="Numeric",
        max_length=10,
        min_length=1,
        max_value=99.0,
        min_value=1.0,
        pattern="[0-9]+",
        start_value=1.0,
        end_value=99.0,
        is_sequence=True,
        interval=2.0,
    )


def _contact() -> Contact:
    return Contact(
        name=_text("CONTACT"),
        department=_text("DEPT"),
        role=_text("ROLE"),
        phones=("+1-555-0100",),
        faxes=("+1-555-0101",),
        x400s=("x400-address",),
        uris=("https://example.org/contact",),
        emails=("contact@example.org",),
        id="CONTACT-ID",
    )


def _other_region(name: str) -> CubeRegion:
    """The region of `CC_<name>`: one excluded, with an after-period, so both defaults' opposites are written (#503).

    An included region and an inclusive bound are what every format defaults
    to, and left out; the main constraint holds the rest of the bounds.
    """
    if name != "provision_agreements":
        return CubeRegion(include=True, keys=(KeyValue(dimension_id="DIM", values=frozenset({"PARENT"})),))
    after = KeyValue(
        dimension_id="TIME_PERIOD",
        time_range=TimeRange(start=TimeBound(period="2020-01-01T00:00:00Z", inclusive=False)),
    )
    return CubeRegion(include=False, keys=(KeyValue(dimension_id="DIM", values=frozenset({"PARENT"})), after))


def _header() -> Header:
    """Every element `StructureHeaderType` allows (#480).

    Its contacts have no id: SDMX-ML's message `ContactType` has none, a gap
    `formats.gaps` declares, and maximal means maximal within the grammar.
    """
    contact = attrs.evolve(_contact(), id=None)
    return Header(
        id="MAXIMAL",
        test=True,
        prepared=datetime(2026, 10, 20, 12, 30, 45, tzinfo=UTC),
        sender=Sender(id="SENDER", name=_text("SENDER"), contacts=(contact,), timezone="-05:00"),
        receivers=(
            Party(id="RECEIVER", name=_text("RECEIVER"), contacts=(contact,)),
            Party(id="RECEIVER2"),
        ),
        name=_text("MESSAGE"),
        source=_text("SOURCE"),
    )


#: An extension's selection that excludes every code of `CL`: its one root,
#: with what is under it. Not the ``%`` wildcard, which SDMX-JSON's
#: `MemberValueType` (a plain id) cannot carry.
_INHERIT_NONE = CodeSelection(
    mode=InclusionMode.EXCLUDE,
    members=(MemberValue(value="PARENT", cascade=CascadeSelection.INCLUDE_DESCENDANTS),),
)


def message() -> StructureMessage:
    """The maximal message. One artefact per slot, every field populated."""
    codelist = Codelist(
        **_maintainable_21(
            "CL",
            is_partial=True,
            codes=[
                Code(
                    id="PARENT",
                    name=_text("CODE-PARENT-NAME"),
                    description=_text("CODE-PARENT-DESC"),
                    annotations=_annotations("CODE-PARENT"),
                    uri="https://example.org/uri/code-parent",
                    links=_links("code-parent"),
                ),
                Code(
                    id="CHILD",
                    name=_text("CODE-CHILD-NAME"),
                    description=_text("CODE-CHILD-DESC"),
                    parent_id="PARENT",
                    annotations=_annotations("CODE-CHILD"),
                    uri="https://example.org/uri/code-child",
                    links=_links("code-child"),
                ),
            ],
        )
    )
    extended = Codelist(
        **_maintainable_21(
            "CL_EXT",
            is_partial=True,
            codes=[Code(id="X", name=_text("X-NAME"))],
            extensions=[
                CodelistExtension(
                    codelist=Ref.to(codelist),
                    selection=CodeSelection(
                        mode=InclusionMode.INCLUDE,
                        members=(MemberValue(value="PARENT", cascade=CascadeSelection.INCLUDE_DESCENDANTS),),
                    ),
                    prefix="PFX_",
                )
            ],
        )
    )
    # SDMX 3.x's geographic codelists (#422): a codelist each, with codes of
    # their own class. 2.1 writes both as plain codelists — a declared gap.
    geographic = GeographicCodelist(
        **_maintainable_21(
            "CL_GEO",
            is_partial=True,
            codes=[
                GeoFeatureSetCode(
                    id="EU",
                    name=_text("GEO-EU-NAME"),
                    description=_text("GEO-EU-DESC"),
                    annotations=_annotations("GEO-EU"),
                    uri="https://example.org/uri/geo-eu",
                    links=_links("geo-eu"),
                    value="POLYGON((0 0, 10 0, 10 10, 0 0))",
                ),
                GeoFeatureSetCode(
                    id="BE",
                    name=_text("GEO-BE-NAME"),
                    description=_text("GEO-BE-DESC"),
                    parent_id="EU",
                    annotations=_annotations("GEO-BE"),
                    uri="https://example.org/uri/geo-be",
                    links=_links("geo-be"),
                    value="POLYGON((0 0, 1 0, 1 1, 0 0))",
                ),
            ],
            # Excludes every code it extends (#421): 2.1 inlines what an
            # extension inherits, and a count changed by inherited codes would
            # hide what 2.1 loses from each of this codelist's own — the
            # census stops at a length mismatch. `CL_EXT` covers inheriting.
            extensions=[CodelistExtension(codelist=Ref.to(codelist), selection=_INHERIT_NONE, prefix="GEO_")],
        )
    )
    grid = GeoGridCodelist(
        **_maintainable_21(
            "CL_GRID",
            is_partial=True,
            codes=[
                GeoGridCode(
                    id="CELL",
                    name=_text("GRID-CELL-NAME"),
                    description=_text("GRID-CELL-DESC"),
                    annotations=_annotations("GRID-CELL"),
                    uri="https://example.org/uri/grid-cell",
                    links=_links("grid-cell"),
                    geo_cell="1kmN2599E4498",
                ),
                GeoGridCode(
                    id="SUB",
                    name=_text("GRID-SUB-NAME"),
                    parent_id="CELL",
                    geo_cell="100mN25990E44980",
                ),
            ],
            # As `CL_GEO`'s: inherits nothing, so the codes compared stay its own.
            extensions=[CodelistExtension(codelist=Ref.to(codelist), selection=_INHERIT_NONE)],
        ),
        grid_definition="1kmN[0-9]{4}E[0-9]{4}",
    )
    # SDMX 3.x's value list (#422), and a concept scheme whose concept it
    # enumerates. 2.1 has neither the class nor a way to name it, so a 2.1 write
    # leaves both out (`sdmxlib.dropped`); the census clears them for 2.1.
    value_list = ValueList(
        **_maintainable(
            "VL",
            items=[
                ValueItem(
                    id="kg",
                    name=_text("VL-KG-NAME"),
                    description=_text("VL-KG-DESC"),
                    annotations=_annotations("VL-KG"),
                ),
                # A value need not be an SDMX id, which is the point of the class.
                ValueItem(id="m 2"),
            ],
        )
    )
    enumerated_by_value_list = ConceptScheme(
        **_maintainable_21(
            "CS_VL",
            is_partial=True,
            concepts=[Concept(id="UNIT", name=_text("CONCEPT-UNIT-NAME"), core_representation=Ref.to(value_list))],
        )
    )
    concepts = ConceptScheme(
        **_maintainable_21(
            "CS",
            is_partial=True,
            concepts=[
                Concept(
                    id="PARENT",
                    name=_text("CONCEPT-PARENT-NAME"),
                    description=_text("CONCEPT-PARENT-DESC"),
                    annotations=_annotations("CONCEPT-PARENT"),
                    uri="https://example.org/uri/concept-parent",
                    links=_links("concept-parent"),
                ),
                Concept(
                    id="CHILD",
                    name=_text("CONCEPT-CHILD-NAME"),
                    description=_text("CONCEPT-CHILD-DESC"),
                    parent_id="PARENT",
                    core_representation=attrs.evolve(_facet(), sentinel_values=_sentinels("CONCEPT")),
                    annotations=_annotations("CONCEPT-CHILD"),
                    uri="https://example.org/uri/concept-child",
                    links=_links("concept-child"),
                ),
                # Not multilingual where 2.1 and 3.0 default it so, beside
                # CHILD's multilingual one, so each version writes the
                # attribute somewhere (#503). No component names it: one that
                # did would inherit the format.
                Concept(
                    id="PLAIN",
                    name=_text("CONCEPT-PLAIN-NAME"),
                    core_representation=Facet(type=DataType.String, is_multilingual=False),
                ),
                # Enumerated, so its core representation can carry an
                # enumeration format, and with an ISO 11179 counterpart (#422).
                # A concept no component names: one that did would hand it the
                # codelist in place of a representation of its own.
                Concept(
                    id="CODED",
                    name=_text("CONCEPT-CODED-NAME"),
                    min_occurs=0,
                    max_occurs=None,
                    core_representation=Ref.to(codelist),
                    enumeration_format=_enumeration_facet(),
                    iso_concept_reference=ISOConceptReference(
                        agency="ISO", concept_scheme_id="ISO_SCHEME", concept_id="ISO_CONCEPT"
                    ),
                ),
            ],
        )
    )
    dsd = DataStructure(
        **_maintainable_21(
            "DSD",
            dimensions=[
                Dimension(
                    id="DIM",
                    concept=_concept_ref("PARENT"),
                    representation=Ref.to(codelist),
                    enumeration_format=_enumeration_facet(),
                    annotations=_annotations("DIM"),
                    concept_role=("role-dim",),
                    uri="https://example.org/uri/dim",
                    links=_links("dim"),
                )
            ],
            time_dimension=TimeDimension(
                id="TIME_PERIOD",
                concept=_concept_ref("PARENT"),
                # `TimeTextFormatType` is a restriction permitting only
                # `textType` (a `TimeDataType`), `startTime` and `endTime`;
                # length, value bounds, pattern, isSequence and the rest are
                # `use="prohibited"`. A full `_facet()` here is not a
                # conformant time dimension, so it tests the writer against
                # input no valid document can carry.
                representation=Facet(
                    type="ObservationalTimePeriod",
                    # A time format's bounds (#422).
                    start_time="2020-01",
                    end_time="2030-12",
                    sentinel_values=_sentinels("TIME"),
                ),
                annotations=_annotations("TD"),
                uri="https://example.org/uri/td",
                links=_links("td"),
            ),
            # One attribute per relationship kind. SDMX types
            # `AttributeRelationship` as a **choice** — dimensions, or the
            # measure, or a group, or the dataset — so an attribute carrying
            # all three at once is not expressible, and a writer that emits one
            # of them is right. Setting all three on one attribute made the
            # census report two false losses that real BIS data disproved.
            attributes=[
                Attribute(
                    id="ATTR",
                    usage=AttributeUsage.MANDATORY,
                    attachment=AttachmentLevel.SERIES,
                    related_dimensions=["DIM"],
                    optional_dimensions={"DIM"},
                    # Not "all measures" (None): a list, so a format that
                    # drops the relationship shows a loss (#422).
                    related_measures=("OBS_VALUE",),
                    concept=_concept_ref("PARENT"),
                    representation=Ref.to(codelist),
                    enumeration_format=_enumeration_facet(),
                    min_occurs=0,
                    max_occurs=None,
                    annotations=_annotations("ATTR"),
                    concept_role=("role-attr",),
                    uri="https://example.org/uri/attr",
                    links=_links("attr"),
                ),
                Attribute(
                    id="ATTR_MEAS",
                    usage=AttributeUsage.OPTIONAL,
                    attachment=AttachmentLevel.OBSERVATION,
                    concept=_concept_ref("PARENT"),
                    annotations=_annotations("ATTR-MEAS"),
                    uri="https://example.org/uri/attr-meas",
                    links=_links("attr-meas"),
                ),
                Attribute(
                    id="ATTR_GRP",
                    usage=AttributeUsage.OPTIONAL,
                    attachment=AttachmentLevel.GROUP,
                    related_group="GRP",
                    concept=_concept_ref("PARENT"),
                    annotations=_annotations("ATTR-GRP"),
                    uri="https://example.org/uri/attr-grp",
                    links=_links("attr-grp"),
                ),
            ],
            measures=[
                # `OBS_VALUE` because SDMX-ML 2.1 pins `PrimaryMeasure/@id` to
                # that exact value — both on the element and on any `<Ref>` to
                # it. 3.0 permits any id, so one conformant fixture has to use
                # the name 2.1 requires.
                Measure(
                    id="OBS_VALUE",
                    usage="mandatory",
                    concept=_concept_ref("PARENT"),
                    representation=Ref.to(codelist),
                    enumeration_format=_enumeration_facet(),
                    max_occurs=3,
                    annotations=_annotations("MEAS"),
                    concept_role=("role-meas",),
                    uri="https://example.org/uri/meas",
                    links=_links("meas"),
                )
            ],
            # The 2.1-era measure dimension. SDMX 3.0 removed it, so this is
            # expected to be lost there — which is the point of setting it.
            measure_dimension=MeasureDimension(
                id="MEASURE_DIM",
                concept=_concept_ref("PARENT"),
                representation=Ref.to(concepts),
                annotations=_annotations("MEASDIM"),
                concept_role=("role-measdim",),
                uri="https://example.org/uri/measdim",
            ),
            groups=[
                Group(
                    id="GRP",
                    dimension_ids=["DIM"],
                    annotations=_annotations("GRP"),
                    uri="https://example.org/uri/grp",
                )
            ],
            # The component lists' own (#422).
            dimension_descriptor_annotations=_annotations("DIMLIST"),
            attribute_descriptor_annotations=_annotations("ATTRLIST"),
            measure_descriptor_annotations=_annotations("MEASLIST"),
            dimension_descriptor_uri="https://example.org/uri/dimlist",
            dimension_descriptor_links=_links("dimlist"),
            attribute_descriptor_uri="https://example.org/uri/attrlist",
            attribute_descriptor_links=_links("attrlist"),
            measure_descriptor_uri="https://example.org/uri/measlist",
            measure_descriptor_links=_links("measlist"),
            evolving_structure=True,
            # The MSD below; by URN because it is built later (#422).
            metadata=MetadataStructure.urn_for(agency=AGENCY, id="MSD", version=VERSION),
            metadata_attributes=[
                MetadataAttributeUsage(
                    id="MA",
                    attachment=AttachmentLevel.SERIES,
                    related_dimensions=["DIM"],
                    optional_dimensions={"DIM"},
                    annotations=_annotations("MAU"),
                    uri="https://example.org/uri/mau",
                    # SDMX-ML 3.x allows a usage one link.
                    links=_links("mau")[:1],
                ),
                # The other members of the attachment choice, as for `attributes`.
                MetadataAttributeUsage(
                    id="MA_CHILD",
                    attachment=AttachmentLevel.GROUP,
                    related_group="GRP",
                    annotations=_annotations("MAU-GRP"),
                ),
            ],
        )
    )
    dataflow = Dataflow(**_maintainable_21("DF", structure=Ref.to(dsd), dimension_constraint=("DIM",)))
    category_scheme = CategoryScheme(
        **_maintainable_21(
            "CATS",
            is_partial=True,
            categories=[
                Category(
                    id="TOP",
                    name=_text("CAT-TOP-NAME"),
                    description=_text("CAT-TOP-DESC"),
                    annotations=_annotations("CAT-TOP"),
                    uri="https://example.org/uri/cat-top",
                    links=_links("cat-top"),
                    categories=[
                        Category(
                            id="SUB",
                            name=_text("CAT-SUB-NAME"),
                            description=_text("CAT-SUB-DESC"),
                            annotations=_annotations("CAT-SUB"),
                            uri="https://example.org/uri/cat-sub",
                            links=_links("cat-sub"),
                        )
                    ],
                )
            ],
        )
    )
    categorisation = Categorisation(
        **_maintainable_21(
            "CATN",
            source=Ref.to(dataflow),
            # A categorisation targets a *Category*, not the scheme holding
            # it: `CategoryReferenceType` pins the URN to
            # `…categoryscheme.Category=`. Targeting the scheme produced a URN
            # every conformant parser rejects (#364).
            target=_item_ref(Category, package="categoryscheme", scheme="CATS", item_id="TOP"),
        )
    )
    agencies = AgencyScheme(
        **_maintainable(
            "AGENCIES",
            is_partial=True,
            agencies=[
                Agency(
                    id="A1",
                    name=_text("AGENCY-NAME"),
                    description=_text("AGENCY-DESC"),
                    contacts=(_contact(),),
                    annotations=_annotations("AGENCY"),
                    uri="https://example.org/uri/agency",
                    links=_links("agency"),
                )
            ],
        )
    )
    providers = DataProviderScheme(
        **_maintainable(
            "DATA_PROVIDERS",
            is_partial=True,
            providers=[
                DataProvider(
                    id="P1",
                    name=_text("PROVIDER-NAME"),
                    description=_text("PROVIDER-DESC"),
                    contacts=(_contact(),),
                    annotations=_annotations("PROVIDER"),
                    uri="https://example.org/uri/provider",
                    links=_links("provider"),
                )
            ],
        )
    )
    # Another agency's: SDMX-ML 2.1 writes metadata providers into their
    # agency's DATA_PROVIDERS scheme (#421), and the scheme properties of two
    # schemes sharing one element must agree — which every field set off its
    # default, as here, would not. Alone it round-trips whole.
    metadata_providers = MetadataProviderScheme(
        **_maintainable(
            "METADATA_PROVIDERS",
            maintainer=METADATA_AGENCY,
            is_partial=True,
            providers=[
                MetadataProvider(
                    id="MP1",
                    name=_text("MDPROVIDER-NAME"),
                    description=_text("MDPROVIDER-DESC"),
                    contacts=(_contact(),),
                    annotations=_annotations("MDPROVIDER"),
                    uri="https://example.org/uri/mdprovider",
                    links=_links("mdprovider"),
                )
            ],
        )
    )
    consumers = DataConsumerScheme(
        **_maintainable(
            "DATA_CONSUMERS",
            is_partial=True,
            consumers=[
                DataConsumer(
                    id="C1",
                    name=_text("CONSUMER-NAME"),
                    description=_text("CONSUMER-DESC"),
                    contacts=(_contact(),),
                    annotations=_annotations("CONSUMER"),
                    uri="https://example.org/uri/consumer",
                    links=_links("consumer"),
                )
            ],
        )
    )
    # A tree, so `parent_id` is held off its default: the one organisation item
    # with a hierarchy (#469).
    units = OrganisationUnitScheme(
        **_maintainable(
            "UNITS",
            is_partial=True,
            organisation_units=[
                OrganisationUnit(
                    id="U1",
                    name=_text("UNIT-NAME"),
                    description=_text("UNIT-DESC"),
                    contacts=(_contact(),),
                    annotations=_annotations("UNIT"),
                    uri="https://example.org/uri/unit",
                    links=_links("unit"),
                ),
                OrganisationUnit(id="U2", name=_text("SUBUNIT-NAME"), parent_id="U1"),
            ],
        )
    )
    provision = ProvisionAgreement(
        **_maintainable_21(
            "PA",
            dataflow=Ref.to(dataflow),
            data_provider=_item_ref(DataProvider, package="base", scheme="DATA_PROVIDERS", item_id="P1"),
        )
    )
    constraint = DataConstraint(
        **_maintainable_21(
            "CC",
            constraint_type=ConstraintType.ALLOWED,
            # One anchor. `ConstraintAttachment` is a choice too — a
            # constraint attaches to a dataflow, or a structure, or a provision
            # agreement, or a provider. The other three are exercised by
            # `_other_attachments` below, one constraint each.
            attachment=ConstraintAttachment(dataflows=(Ref.to(dataflow),)),
            regions=[
                CubeRegion(
                    include=True,
                    annotations=_annotations("REGION"),
                    keys=(
                        # Every qualifier a dimension's value set and value
                        # may carry, each off its default (#422).
                        KeyValue(
                            dimension_id="DIM",
                            values=[
                                RegionValue(
                                    "PARENT",
                                    cascade=CascadeSelection.INCLUDE_DESCENDANTS,
                                    valid_from="2020-01-01",
                                    valid_to="2030-12-31",
                                )
                            ],
                            remove_prefix=True,
                            valid_from="2020-01-01",
                            valid_to="2030-12-31",
                        ),
                        # A time range rather than values (#433). One bound
                        # exclusive, so a writer dropping `isInclusive="false"`
                        # — the non-default — is caught.
                        KeyValue(
                            dimension_id="TIME_PERIOD",
                            include=False,
                            # Zoned: SDMX-JSON refuses a date-time without
                            # one, which SDMX-ML allows (#496).
                            time_range=TimeRange(
                                start=TimeBound(period="2020-01-01T00:00:00Z", inclusive=False),
                                # Exclusive too: an inclusive bound is the default, left out (#503).
                                end=TimeBound(period="2030-12-31T00:00:00Z", inclusive=False),
                                # The range's own validity, apart from the
                                # value set's — SDMX 3.x only (#422).
                                valid_from="2021-01-01",
                                valid_to="2029-12-31",
                            ),
                        ),
                    ),
                    components=(
                        # Excluding, the non-default, so a format that
                        # drops `include="false"` shows a loss (#422).
                        ComponentValueSet(
                            component_id="ATTR",
                            # `INCLUDE_DESCENDANTS`: not `EXCLUDE_ROOT`, which
                            # 2.1 has no form for and refuses, and not
                            # `SELF_ONLY`, the default every format leaves out (#503).
                            values=[RegionValue("CHILD", cascade=CascadeSelection.INCLUDE_DESCENDANTS, lang="en")],
                            include=False,
                            remove_prefix=True,
                        ),
                        # End only: the `BeforePeriod` form.
                        ComponentValueSet(
                            component_id="ATTR_MEAS",
                            time_range=TimeRange(end=TimeBound(period="2025-06-30T00:00:00Z", inclusive=False)),
                        ),
                    ),
                )
            ],
            reference_period=ReferencePeriod(
                start_period=datetime(2020, 1, 1),
                end_period=datetime(2030, 12, 31),
            ),
            release_calendar=ReleaseCalendar(periodicity="P1M", offset="P0D", tolerance="P5D"),
        )
    )
    # Key sets in a constraint of their own: the IM gives a data constraint
    # cube regions or key sets, not both (§12.3.3.1, #491), and FMR refuses
    # the two together.
    key_set_constraint = DataConstraint(
        **_maintainable_21(
            "CC_KEYS",
            constraint_type=ConstraintType.ALLOWED,
            attachment=ConstraintAttachment(dataflows=(Ref.to(dataflow),)),
            # One set of each kind: an excluded set read as allowed until #479.
            data_key_sets=[
                DataKeySet(
                    is_included=True,
                    keys=[
                        DataKey(
                            values=(("DIM", "PARENT"),),
                            annotations=_annotations("KEY"),
                            valid_from="2020-01-01",
                            valid_to="2030-12-31",
                            # The attribute values the key's data has (#422).
                            components=(
                                ComponentValueSet(
                                    component_id="ATTR",
                                    values=[RegionValue("CHILD", cascade=CascadeSelection.EXCLUDE_ROOT, lang="en")],
                                    include=False,
                                    remove_prefix=True,
                                ),
                                # Values or a time range, never both: the schema's choice.
                                ComponentValueSet(
                                    component_id="ATTR_MEAS",
                                    time_range=TimeRange(start=TimeBound("2020-01"), end=TimeBound("2030-12")),
                                ),
                            ),
                        )
                    ],
                ),
                DataKeySet(is_included=False, keys=[DataKey(values=(("DIM", "CHILD"),))]),
            ],
        )
    )
    other_constraints = [
        DataConstraint(
            **_maintainable_21(
                f"CC_{name}",
                constraint_type=ConstraintType.ALLOWED,
                attachment=attachment,
                regions=[_other_region(name)],
            )
        )
        for name, attachment in (
            ("data_structures", ConstraintAttachment(data_structures=(Ref.to(dsd),))),
            ("provision_agreements", ConstraintAttachment(provision_agreements=(Ref.to(provision),))),
            (
                "data_provider",
                ConstraintAttachment(
                    data_provider=_item_ref(DataProvider, package="base", scheme="DATA_PROVIDERS", item_id="P1")
                ),
            ),
        )
    ]
    representation_map = RepresentationMap(
        **_maintainable(
            "RM",
            **_IN_SS,
            id="RM@SS",
            # One codelist each side and a literal 1:1 mapping: the shape 2.1
            # can write. The 3.x-only shapes — N:M, data types, regex and
            # index values — are `shapes.message()`'s, since 2.1 refuses them.
            sources=[Ref.to(codelist)],
            targets=[Ref.to(codelist)],
            mappings=[
                # `date`, not `datetime` — the XSD types a mapping's validity
                # `xs:date` where a maintainable's is `xs:dateTime`, and the
                # model follows it. attrs does not validate, so passing a
                # datetime here is accepted and only fails at read-back.
                RepresentationMapping(
                    sources=["PARENT"],
                    targets=["CHILD"],
                    valid_from=date(2020, 1, 1),
                    valid_to=date(2030, 12, 31),
                    annotations=_annotations("VALUEMAP"),
                ),
            ],
        )
    )
    # A value map as an SDMX-ML 2.1 reader builds one from a component map's
    # inline <ValueMap> (#422): data type to data type, marked, named after the
    # structure map and the component map's position, and nothing else set —
    # so a 2.1 write puts it back inline and the read builds it again, equal.
    value_map = RepresentationMap(
        # Its structure map's set's, as the reader gives every map in a set
        # (#421) — the built one too.
        id="SM_VALUE_MAP_2@SS",
        maintainer=AGENCY,
        version=VERSION,
        valid_from=datetime(2020, 1, 1),
        valid_to=datetime(2030, 12, 31),
        is_external_reference=True,
        **_IN_SS,
        name=_text("SM-NAME"),
        annotations=Annotations([Annotation(type=BUILT_VALUE_MAP_TYPE)]),
        sources=[MapDataType("String")],
        targets=[MapDataType("String")],
        mappings=[RepresentationMapping(sources=["1"], targets=["M"])],
    )
    structure_map = StructureMap(
        **_maintainable(
            "SM",
            **_IN_SS,
            id="SM@SS",
            source=Ref.to(dsd),
            target=Ref.to(dsd),
            is_extension=True,
            maps=[
                ComponentMap(
                    sources=["DIM"],
                    targets=["DIM"],
                    annotations=_annotations("COMPMAP"),
                    # SDMX 3.x's value map for a component (#422); in 2.1 a
                    # <CodelistMap> of the same structure set.
                    representation_map=Ref.to(representation_map),
                ),
                # 2.1's inline <ValueMap>, and its <ToTextFormat> and
                # <ToValueType>, which only 2.1 has (#422).
                ComponentMap(sources=["DIM"], targets=["DIM"], representation_map=Ref.to(value_map)),
                ComponentMap(
                    sources=["DIM"],
                    targets=["DIM"],
                    # Not multilingual, which 2.1's TextFormatType defaults true (#503).
                    to_text_format=attrs.evolve(_facet(), is_multilingual=False),
                    to_value_type=ToValueType.NAME,
                ),
            ],
            # SDMX 3.x's date and value maps (#422): each choice branch once —
            # an epoch map by frequency dimension, a date-pattern map by
            # target frequency, a fixed value for a source and for a target.
            epoch_maps=[
                EpochMap(
                    id="EPOCH",
                    component_maps=[("TIME_PERIOD", "TIME_PERIOD")],
                    base_period="1970-01-01",
                    epoch_period=EpochPeriod.DAY,
                    frequency_dimension="DIM",
                    mapped_frequencies=("FFM",),
                    resolve_period=ResolvePeriod.START_OF_PERIOD,
                    annotations=_annotations("EPOCH"),
                    uri="https://example.org/uri/epoch",
                    links=_links("epoch"),
                ),
                EpochMap(
                    id="EPOCH_M",
                    component_maps=[("TIME_PERIOD", "TIME_PERIOD")],
                    base_period="2000-01-01",
                    epoch_period=EpochPeriod.SECOND,
                    target_frequency_id="M",
                ),
            ],
            date_pattern_maps=[
                DatePatternMap(
                    id="PATTERN",
                    component_maps=[("TIME_PERIOD", "TIME_PERIOD")],
                    source_pattern="yyyyMM",
                    locale="en",
                    target_frequency_id="M",
                    resolve_period=ResolvePeriod.END_OF_PERIOD,
                    annotations=_annotations("PATTERN"),
                    uri="https://example.org/uri/pattern",
                    links=_links("pattern"),
                ),
                DatePatternMap(
                    id="PATTERN_BY_FREQ",
                    component_maps=[("TIME_PERIOD", "TIME_PERIOD")],
                    source_pattern="dd/MM/yyyy",
                    locale="fr",
                    frequency_dimension="DIM",
                    mapped_frequencies=("FFM",),
                ),
            ],
            frequency_format_mappings=[
                FrequencyFormatMapping(
                    id="FFM",
                    frequency_id="M",
                    date_pattern="yyyy-MM",
                    annotations=_annotations("FFM"),
                    uri="https://example.org/uri/ffm",
                    links=_links("ffm"),
                )
            ],
            fixed_value_maps=[
                FixedValueMap(source_id="ATTR", values=("X",), annotations=_annotations("FIXED")),
                FixedValueMap(target_id="ATTR", values=("Y", "Z")),
            ],
        )
    )
    concept_scheme_map = ConceptSchemeMap(
        **_maintainable(
            "CSM",
            **_IN_SS,
            id="CSM@SS",
            source=Ref.to(concepts),
            target=Ref.to(concepts),
            maps=[
                ItemMap(
                    source_id="PARENT",
                    target_id="CHILD",
                    annotations=_annotations("ITEMMAP"),
                    valid_from=date(2020, 1, 1),
                    valid_to=date(2030, 12, 31),
                )
            ],
        )
    )
    # Agencies onto data providers: an organisation scheme map's ends may be
    # schemes of different kinds (#448).
    organisation_scheme_map = OrganisationSchemeMap(
        **_maintainable(
            "OSM",
            **_IN_SS,
            id="OSM@SS",
            source=Ref.to(agencies),
            target=Ref.to(providers),
            maps=[
                ItemMap(
                    source_id=agencies.agencies[0].id,
                    target_id="P1",
                    annotations=_annotations("ORGMAP"),
                    valid_from=date(2020, 1, 1),
                    valid_to=date(2030, 12, 31),
                )
            ],
        )
    )
    level = Level(
        id="L1",
        name=_text("LEVEL-NAME"),
        description=_text("LEVEL-DESC"),
        coding_format=_coding_facet(),
        annotations=_annotations("LEVEL"),
        uri="https://example.org/uri/level",
        links=_links("level"),
    )
    # A second level, so a format that writes levels as a chain is held to it:
    # SDMX-ML 3.x wrote siblings, XSD-invalid past one (#422).
    second_level = Level(
        id="L2",
        name=_text("LEVEL-2-NAME"),
        description=_text("LEVEL-2-DESC"),
        coding_format=_coding_facet(),
        annotations=_annotations("LEVEL-2"),
        uri="https://example.org/uri/level-2",
        links=_links("level-2"),
    )
    hierarchy = Hierarchy(
        **_maintainable(
            "H",
            # What a 2.1 reader gives a hierarchy in `hierarchical_codelist`
            # below (#421): the id `member_id` composes, and the codelist's
            # external reference, as its validity already is.
            id="H@HCL",
            service_url="https://example.org/registry/HCL",
            structure_url="https://example.org/structure/HCL.xml",
            has_formal_levels=True,
            levels=[level, second_level],
            tree=[
                HierarchicalCode(
                    id="NODE",
                    version="1.0",
                    code=_item_ref(Code, package="codelist", scheme="CL", item_id="PARENT"),
                    annotations=_annotations("HCODE"),
                    valid_from=datetime(2020, 1, 1),
                    valid_to=datetime(2030, 12, 31),
                    level=level,
                    uri="https://example.org/uri/hcode",
                    links=_links("hcode"),
                    children=[
                        HierarchicalCode(
                            id="LEAF",
                            code=_item_ref(Code, package="codelist", scheme="CL", item_id="CHILD"),
                            annotations=_annotations("HCODE-LEAF"),
                            level=second_level,
                            uri="https://example.org/uri/hcode-leaf",
                            links=_links("hcode-leaf"),
                        )
                    ],
                )
            ],
        )
    )
    hierarchy_association = HierarchyAssociation(
        **_maintainable(
            "HA",
            linked_hierarchy=Ref.to(hierarchy),
            linked_object=Ref.to(dsd),
            context_object=Ref.to(dataflow),
        )
    )
    msd = MetadataStructure(
        **_maintainable_21(
            "MSD",
            attributes=[
                MetadataAttribute(
                    id="MA",
                    concept=_concept_ref("PARENT"),
                    representation=Ref.to(codelist),
                    enumeration_format=_enumeration_facet(),
                    min_occurs=1,
                    max_occurs=9,
                    is_presentational=True,
                    annotations=_annotations("MA"),
                    uri="https://example.org/uri/ma",
                    links=_links("ma"),
                    children=[
                        MetadataAttribute(
                            id="MA_CHILD",
                            concept=_concept_ref("CHILD"),
                            annotations=_annotations("MA-CHILD"),
                            uri="https://example.org/uri/ma-child",
                            links=_links("ma-child"),
                        )
                    ],
                )
            ],
            targets=[
                MetadataTarget(
                    id="TARGET",
                    target_objects=[
                        TargetObject(
                            id="COMP",
                            kind="IdentifiableObjectTarget",
                            object_type="DataStructure",
                            # 2.1 types a target's format `TargetObjectDataType`,
                            # whose textType is one of KeyValues /
                            # IdentifiableReference / DataSetReference /
                            # AttachmentConstraintReference — not a general facet.
                            representation=Facet(type="IdentifiableReference"),
                            annotations=_annotations("TARGET-COMP"),
                            uri="https://example.org/uri/target-comp",
                        ),
                        # A second target object (#422): 2.1 lets a target
                        # combine kinds — an object *within* a data set.
                        TargetObject(
                            id="DATA_SET_TARGET",
                            kind="DataSetTarget",
                            representation=Facet(type="DataSetReference"),
                        ),
                    ],
                    annotations=_annotations("TARGET"),
                    uri="https://example.org/uri/target",
                ),
                # The other four kinds, each with the id and text type 2.1 fixes
                # for it — never written until the 2.1 census asked (#422).
                *(
                    MetadataTarget(
                        id=f"TARGET_{kind.upper()}",
                        target_objects=[
                            TargetObject(
                                id=component_id,
                                kind=kind,
                                representation=Facet(type=text_type),
                                # Every kind element is annotable (#422); one
                                # plain annotation each, so 2.1's annotation
                                # limits stay at the first target.
                                annotations=Annotations([Annotation(type=kind)]),
                                uri=f"https://example.org/uri/{component_id.lower()}",
                            )
                        ],
                    )
                    for kind, component_id, text_type in (
                        ("KeyDescriptorValuesTarget", "DIMENSION_DESCRIPTOR_VALUES_TARGET", "KeyValues"),
                        ("DataSetTarget", "DATA_SET_TARGET", "DataSetReference"),
                        ("ConstraintContentTarget", "CONSTRAINT_CONTENT_TARGET", "AttachmentConstraintReference"),
                        ("ReportPeriodTarget", "REPORT_PERIOD_TARGET", "ObservationalTimePeriod"),
                    )
                ),
            ],
            attribute_list_annotations=_annotations("MALIST"),
            attribute_list_uri="https://example.org/uri/malist",
            attribute_list_links=_links("malist"),
            report_structures=[
                ReportStructure(
                    id="REPORT",
                    attribute_ids=("MA",),
                    target_ids=("TARGET",),
                    annotations=_annotations("REPORT"),
                    uri="https://example.org/uri/report",
                )
            ],
        )
    )
    # `Metadataflow.targets` is `tuple[str, ...]` on purpose, not `Ref`: SDMX
    # allows a wildcard URN here (`WildcardUrnType`), which no resolvable
    # reference can express.
    metadataflow = Metadataflow(
        **_maintainable_21(
            "MDF",
            structure=Ref.to(msd),
            targets=("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:DF(1.0)",),
        )
    )
    # The XSD pins the provider scheme's id: `MetadataProviderReferenceType`
    # matches `.+\.base\.MetadataProvider=.+:METADATA_PROVIDERS\(.+\).+`.
    metadata_provision = MetadataProvisionAgreement(
        **_maintainable(
            "MPA",
            metadataflow=Ref.to(metadataflow),
            # `MetadataProviderReferenceType` pins this to
            # `…base.MetadataProvider=…:METADATA_PROVIDERS(…)`. sdmxlib does
            # not model `MetadataProvider` (#315), so the class is named
            # explicitly rather than taken from a stand-in type — passing
            # `DataProvider` produced a URN the schema rejects.
            metadata_provider=Ref[object].unresolved(
                SdmxUrn.make(
                    object,
                    package="base",
                    artefact_class="MetadataProvider",
                    agency=METADATA_AGENCY,
                    id="METADATA_PROVIDERS",
                    version=VERSION,
                    item_id="MP1",
                )
            ),
            targets=("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:DF(*)",),
        )
    )
    # The 2.1 container (#434). Absent until then, so neither field census had
    # ever looked at one — which is how `included_codelists` and `isFinal`
    # were lost on read and on a store round-trip with nothing noticing.
    hierarchical_codelist = HierarchicalCodelist(
        **_without_links(
            _maintainable_21(
                "HCL",
                hierarchies=(Ref.to(hierarchy),),
                included_codelists=(Ref.to(codelist),),
            )
        )
    )
    # The other 2.1 container (#239), absent for the same reason: the 2.1
    # census found `related_structures` neither written nor read, and
    # `isFinal` not read (#422).
    structure_set = StructureSet(
        **_without_links(
            _maintainable_21(
                "SS",
                maps=(
                    Ref.to(representation_map),
                    Ref.to(structure_map),
                    Ref.to(concept_scheme_map),
                    Ref.to(organisation_scheme_map),
                ),
                related_structures=(Ref.to(dsd),),
            )
        )
    )
    return StructureMessage(
        header=_header(),
        codelists=[codelist, extended, geographic, grid],
        value_lists=[value_list],
        concept_schemes=[concepts, enumerated_by_value_list],
        data_structures=[dsd],
        dataflows=[dataflow],
        category_schemes=[category_scheme],
        categorisations=[categorisation],
        provision_agreements=[provision],
        agency_schemes=[agencies],
        data_provider_schemes=[providers],
        metadata_provider_schemes=[metadata_providers],
        data_consumer_schemes=[consumers],
        organisation_unit_schemes=[units],
        constraints=[constraint, key_set_constraint, *other_constraints],
        representation_maps=[representation_map, value_map],
        structure_maps=[structure_map],
        concept_scheme_maps=[concept_scheme_map],
        organisation_scheme_maps=[organisation_scheme_map],
        hierarchies=[hierarchy],
        hierarchical_codelists=[hierarchical_codelist],
        structure_sets=[structure_set],
        hierarchy_associations=[hierarchy_association],
        metadata_structures=[msd],
        metadataflows=[metadataflow],
        metadata_provision_agreements=[metadata_provision],
    )
