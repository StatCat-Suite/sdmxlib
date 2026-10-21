"""Modelled shapes the maximal fixture does not write, for the element and JSON censuses (#422).

Each is a *variant* of something `maximal` already exercises field by field — an
exclusive rather than an inclusive selection, a time range open at its end,
data-type rather than codelist map ends, a text-format rather than an
enumerated component — so adding them to that fixture would only duplicate its
checks. The censuses need them *written*, so a schema pair the writer can
produce is not mistaken for one it drops.

Two variants are here rather than in `maximal` because SDMX-ML 2.1 refuses
them (#242) and the field census writes the fixture to every format: a regex
and an index value map.
"""

from datetime import UTC, datetime

import attrs

import sdmxlib as sl
from sdmxlib.model.codelist import CodeSelection, InclusionMode, MemberValue
from sdmxlib.model.constraint import (
    ConstraintAttachment,
    CubeRegion,
    DataKey,
    DataKeySet,
    DataSetReference,
    KeyValue,
    QueryableDataSource,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.datastructure import REPORTING_YEAR_START_DAY, AttachmentLevel
from sdmxlib.model.mapping import ItemMap, MapDataType, RepresentationMapping, SourceValue
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn


def _dimension_facet() -> sl.Facet:
    """Every facet a dimension's text format may carry — its own schema type, which the fixture's never reach."""
    return sl.Facet(
        type="Integer",
        min_length=1,
        max_length=4,
        min_value=1.0,
        max_value=9999.0,
        decimals=2,
        pattern="[0-9]+",
        start_value=1.0,
        end_value=9999.0,
        is_sequence=True,
        interval=1.0,
        time_interval="P1M",
        start_time="2020-01",
        end_time="2030-12",
        sentinel_values=(sl.SentinelValue("-1", name={"en": "Not applicable"}),),
    )


def message() -> StructureMessage:
    """The shapes, in one message. See the module docstring."""
    other = sl.Codelist(id="CL_OTHER", maintainer="AG", name={"en": "o"}, codes=[sl.Code(id="A", name={"en": "a"})])
    plain = sl.Codelist(
        id="CL_SHAPES",
        maintainer="AG",
        name={"en": "s"},
        # Any codelist may hold a geographic code: `Code`'s substitution group.
        codes=[
            sl.Code(id="A", name={"en": "a"}),
            sl.GeoFeatureSetCode(id="F", name={"en": "f"}, value="POINT(0 0)"),
            sl.GeoGridCode(id="G", name={"en": "g"}, geo_cell="1kmN1E1"),
        ],
        extensions=[
            sl.CodelistExtension(
                codelist=Ref.to(other),
                selection=CodeSelection(mode=InclusionMode.EXCLUDE, members=(MemberValue("A"),)),
            )
        ],
    )
    schemes = [
        sl.CategoryScheme(
            id=f"CAT_{side}", maintainer="AG", name={"en": side}, categories=[sl.Category(id="X", name={"en": "x"})]
        )
        for side in ("S", "T")
    ]
    category_map = sl.CategorySchemeMap(
        id="CSM",
        maintainer="AG",
        name={"en": "csm"},
        description={"en": "maps one category scheme onto another"},
        # The maximal fixture has no category scheme map, so its attributes
        # are written here (#422).
        uri="https://example.org/uri/CSM",
        links=(sl.Link(rel="alternate", url="https://example.org/link/CSM"),),
        is_external_reference=True,
        service_url="https://example.org/registry/CSM",
        structure_url="https://example.org/structure/CSM.xml",
        is_partial_language=True,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        valid_to=datetime(2030, 12, 31, tzinfo=UTC),
        annotations=sl.Annotations([sl.Annotation(type="NOTE", title="csm")]),
        source=Ref.to(schemes[0]),
        target=Ref.to(schemes[1]),
        # Annotated: 2.1's `CategoryMap` is the only item map the maximal
        # fixture, which has no category scheme map, does not reach (#422).
        maps=(ItemMap("X", "X", annotations=sl.Annotations([sl.Annotation(type="NOTE", title="x")])),),
    )
    type_map = sl.RepresentationMap(
        id="RM_TYPES",
        maintainer="AG",
        name={"en": "rm"},
        sources=(MapDataType("String"),),
        targets=(MapDataType("Integer"),),
        mappings=(RepresentationMapping(sources=("a",), targets=("1",)),),
    )
    concepts = sl.ConceptScheme(
        id="CS_SHAPES",
        maintainer="AG",
        name={"en": "c"},
        concepts=[
            *(sl.Concept(id=c, name={"en": c}) for c in ("DIM", "ATTR", "OBS_VALUE", "NOTE")),
            # Enumerated by a codelist: the fixture's concepts are free text, or
            # enumerated by a value list 2.1 leaves out.
            sl.Concept(id="CODED", name={"en": "coded"}, core_representation=Ref.to(other)),
        ],
    )
    dsd = sl.DataStructure(
        id="DSD_SHAPES",
        maintainer="AG",
        name={"en": "d"},
        # Each component free text: the fixture's are enumerated.
        dimensions=[sl.Dimension(id="DIM", concept=concepts.ref_to("DIM"), representation=_dimension_facet())],
        # 3.x spells a dataset-level attachment `AttributeRelationship/Dataflow`.
        attributes=[
            sl.Attribute(
                id="ATTR",
                concept=concepts.ref_to("ATTR"),
                attachment=AttachmentLevel.DATASET,
                representation=sl.Facet(type="String"),
            )
        ],
        measures=[
            sl.Measure(
                id="OBS_VALUE",
                concept=concepts.ref_to("OBS_VALUE"),
                representation=sl.Facet(type="Double"),
                min_occurs=0,
            )
        ],
    )
    msd = sl.MetadataStructure(
        id="MSD_SHAPES",
        maintainer="AG",
        name={"en": "m"},
        attributes=[
            sl.MetadataAttribute(id="NOTE", concept=concepts.ref_to("NOTE"), representation=sl.Facet(type="String"))
        ],
    )
    variant_map = sl.RepresentationMap(
        id="RM_VARIANTS",
        maintainer="AG",
        name={"en": "v"},
        sources=(Ref.to(plain),),
        targets=(Ref.to(other),),
        mappings=(
            RepresentationMapping(sources=(SourceValue("F.*", is_regex=True),), targets=("A",)),
            RepresentationMapping(sources=(SourceValue("GX", start_index=0, end_index=1),), targets=("A",)),
        ),
    )
    # N:M (#422): two endpoints a side, a mapping with a value per endpoint, and
    # one with no target at all. A value at a codelist endpoint is one of its
    # codes: FMR refused `B` and `Z`, which neither codelist holds, and nothing
    # here checks it (#457, #483).
    n_to_m_map = sl.RepresentationMap(
        id="RM_N_TO_M",
        maintainer="AG",
        name={"en": "n"},
        sources=(Ref.to(plain), MapDataType("String")),
        targets=(MapDataType("Integer"), Ref.to(other)),
        mappings=(
            RepresentationMapping(sources=("A", SourceValue("^x", is_regex=True)), targets=("1", "A")),
            RepresentationMapping(sources=("A", "Y")),
        ),
    )
    n_to_m_structure_map = sl.StructureMap(
        id="SM_N_TO_M",
        maintainer="AG",
        name={"en": "n"},
        source=Ref.to(dsd),
        target=Ref.to(dsd),
        maps=(
            sl.ComponentMap(sources=("DIM", "ATTR"), targets=("DIM", "ATTR"), representation_map=Ref.to(n_to_m_map)),
        ),
    )
    flow = sl.Dataflow(id="DF_SHAPES", maintainer="AG", name={"en": "f"}, structure=Ref.to(dsd))
    second_flow = sl.Dataflow(id="DF_SHAPES_2", maintainer="AG", name={"en": "f2"}, structure=Ref.to(dsd))
    constraint = sl.DataConstraint(
        id="DC_SHAPES",
        maintainer="AG",
        name={"en": "dc"},
        attachment=ConstraintAttachment(dataflows=(Ref.to(flow),)),
        regions=[
            CubeRegion(keys=(KeyValue(dimension_id="TIME_PERIOD", time_range=TimeRange(start=TimeBound("2020-01"))),))
        ],
    )
    # The attachment alternatives `maximal` cannot hold, since SDMX-ML 3.1
    # and SDMX-JSON have no data sources (#422): several dataflows with queryable sources
    # beside them, and simple data sources.
    queryable = sl.DataConstraint(
        id="DC_QUERYABLE",
        maintainer="AG",
        name={"en": "q"},
        attachment=ConstraintAttachment(
            dataflows=(Ref.to(flow), Ref.to(second_flow)),
            queryable_data_sources=(
                QueryableDataSource(
                    data_url="https://example.org/rest/data",
                    is_rest_datasource=True,
                    is_web_service_datasource=True,
                    wsdl_url="https://example.org/ws?wsdl",
                    wadl_url="https://example.org/rest?wadl",
                ),
            ),
        ),
    )
    simple = sl.DataConstraint(
        id="DC_SIMPLE",
        maintainer="AG",
        name={"en": "s"},
        attachment=ConstraintAttachment(simple_data_sources=("https://example.org/a.xml", "https://example.org/b.xml")),
    )
    return StructureMessage(
        codelists=[other, plain],
        concept_schemes=[concepts],
        category_schemes=schemes,
        category_scheme_maps=[category_map],
        representation_maps=[type_map, variant_map, n_to_m_map],
        structure_maps=[n_to_m_structure_map],
        metadata_structures=[msd],
        data_structures=[dsd],
        dataflows=[flow, second_flow],
        constraints=[constraint, queryable, simple],
    )


def message_31() -> StructureMessage:
    """The shapes SDMX-ML 3.1 and SDMX-JSON can carry: all but the data sources (#422).

    Plus 3.1's availability constraint (#421), which SDMX-JSON leaves out.

    3.1 and SDMX-JSON 2.1 removed them, and the JSON writer's output validates
    against 2.1 as well as 2.0.
    """
    shapes = message()
    # 3.1's own: an availability statement, which has no identity (#421).
    availability = sl.AvailabilityConstraint(
        attachment=ConstraintAttachment(dataflows=(Ref.to(shapes.dataflows[0]),)),
        region=CubeRegion(keys=(KeyValue(dimension_id="FREQ", values=frozenset({"A"})),)),
        series_count=12,
        obs_count=340,
        annotations=[sl.Annotation(id="AVAILABILITY", title="computed")],
    )
    return attrs.evolve(
        shapes,
        constraints=[c for c in shapes.constraints if c.id not in {"DC_QUERYABLE", "DC_SIMPLE"}],
        availability_constraints=[availability],
    )


def message_21() -> StructureMessage:
    """The shapes SDMX-ML 2.1 can carry, and those only it has.

    Less the data-type, regex/index and N:M value maps, which 2.1 refuses
    (#242, #422); the MSD, which has no 2.1 target layer; and the geographic
    codes and codelist extension, which 2.1 writes as plain codes and cannot
    write (`gaps.py`).

    Plus 2.1's own: an `AttachmentConstraint`, which the store refuses and 3.x
    writes as a data constraint (#395), so it cannot join `maximal`; a data
    set attachment; and of a DSD, a group by attachment constraint, an
    attachment group and the reporting year's start day (#422).
    """
    shapes = message()
    flow = shapes.dataflows[0]
    attachment = sl.AttachmentConstraint(
        id="AC_SHAPES",
        maintainer="AG",
        name={"en": "ac"},
        description={"en": "attaches a key to the flow"},
        uri="https://example.org/uri/AC",
        is_external_reference=True,
        service_url="https://example.org/registry/AC",
        structure_url="https://example.org/structure/AC.xml",
        is_partial_language=True,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        valid_to=datetime(2030, 12, 31, tzinfo=UTC),
        # A 1.0 declared final: what 2.1's `isFinal` holds apart from the version.
        legacy_is_final=True,
        annotations=sl.Annotations([sl.Annotation(type="NOTE", title="ac")]),
        attachment=ConstraintAttachment(dataflows=(Ref.to(flow),)),
        data_key_sets=(DataKeySet(is_included=False, keys=(DataKey(values=(("DIM", "1"),)),)),),
    )
    # 2.1 requires `assignmentStatus`: an unstated usage is written Conditional
    # and read back optional, so it is stated.
    (dsd,) = shapes.data_structures
    dsd = attrs.evolve(dsd, attributes=[attrs.evolve(a, usage="optional") for a in dsd.attributes])
    # What only 2.1 has of a DSD (#422): a group defined by an attachment
    # constraint, an attribute's attachment group, and the reporting year's
    # start day — an attribute by its reserved id.
    (concepts,) = shapes.concept_schemes
    dsd = attrs.evolve(
        dsd,
        groups=[
            sl.Group(id="G_DIMS", dimension_ids=["DIM"]),
            sl.Group(id="G_CONSTRAINT", attachment_constraint=Ref.to(attachment)),
        ],
        attributes=[
            *dsd.attributes,
            sl.Attribute(
                id="ATTR_SERIES",
                usage="optional",
                concept=concepts.ref_to("ATTR"),
                attachment=AttachmentLevel.SERIES,
                related_dimensions=["DIM"],
                attachment_groups=("G_DIMS",),
                representation=sl.Facet(type="String"),
            ),
            sl.Attribute(
                id=REPORTING_YEAR_START_DAY,
                usage="optional",
                concept=concepts.ref_to("ATTR"),
                attachment=AttachmentLevel.DATASET,
                representation=sl.Facet(type="MonthDay"),
                annotations=sl.Annotations([sl.Annotation(type="NOTE", title="rysd")]),
                uri="https://example.org/uri/rysd",
            ),
        ],
    )
    codelists = [
        attrs.evolve(cl, codes=[sl.Code(id=code.id, name=code.name) for code in cl.codes], extensions=[])
        for cl in shapes.codelists
    ]
    # A ContentConstraint takes one simple data source, and 2.1 alone a data set.
    constraints = [
        attrs.evolve(c, attachment=ConstraintAttachment(simple_data_sources=c.attachment.simple_data_sources[:1]))
        if c.id == "DC_SIMPLE"
        else c
        for c in shapes.constraints
    ]
    provider = Ref[object].unresolved(
        SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.base.DataProvider=AG:DATA_PROVIDERS(1.0).P")
    )
    data_set = sl.DataConstraint(
        id="DC_DATA_SET",
        maintainer="AG",
        name={"en": "ds"},
        attachment=ConstraintAttachment(data_sets=(DataSetReference(data_provider=provider, id="DS1"),)),
    )
    return attrs.evolve(
        shapes,
        codelists=codelists,
        constraints=[*constraints, data_set],
        data_structures=[dsd],
        representation_maps=[],
        structure_maps=[],
        metadata_structures=[],
        attachment_constraints=[attachment],
        organisation_unit_schemes=[_final_unit_scheme()],
    )


def _final_unit_scheme() -> sl.OrganisationUnitScheme:
    """An organisation unit scheme only 2.1 can write (#469).

    2.1 alone lets an organisation unit scheme carry a version other than 1.0;
    3.x prohibits the attribute and SDMX-JSON fixes it. At a final version it
    shows 2.1's `isFinal`, which the fixed-id schemes cannot.
    """
    return sl.OrganisationUnitScheme(
        id="UNITS_21",
        maintainer="AG",
        version="2.1.0",
        name={"en": "units"},
        organisation_units=[
            sl.OrganisationUnit(id="U1", name={"en": "u1"}),
            sl.OrganisationUnit(id="U2", name={"en": "u2"}, parent_id="U1"),
        ],
    )
