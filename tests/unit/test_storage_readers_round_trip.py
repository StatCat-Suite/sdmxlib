"""Storage round-trip tests for artefact types whose readers were
untested. Closes #125.

Each test puts the artefact into a fresh in-memory DuckDB schema, reads
it back via ``get_artefact``, and asserts that the surviving instance
matches the original on every persisted field.

Covers:

- DataConstraint
- CategoryScheme + Categorisation
- AgencyScheme
- DataProviderScheme
- ProvisionAgreement
- RepresentationMap
- MetadataStructure
"""

import pytest

from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.constraint import (
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    KeyValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import Dimension
from sdmxlib.model.hierarchy import Hierarchy, HierarchyAssociation
from sdmxlib.model.mapping import RepresentationMap, RepresentationMapping
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    DataProvider,
    DataProviderScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.writers import (
    put_agency_scheme,
    put_categorisation,
    put_category_scheme,
    put_data_constraint,
    put_data_provider_scheme,
    put_hierarchy_association,
    put_metadata_structure,
    put_provision_agreement,
    put_representation_map,
)
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _urn(cls: type, agency: str, id_: str, version: str = "1.0") -> str:
    return str(
        SdmxUrn.make(
            cls,
            package=cls.sdmx_package,
            artefact_class=cls.sdmx_class,
            agency=agency,
            id=id_,
            version=version,
        )
    )


# ── DataConstraint ───────────────────────────────────────────────────────────


def test_data_constraint_round_trip(cursor) -> None:
    df_urn: SdmxUrn[Dataflow] = SdmxUrn(
        package="datastructure", artefact_class="Dataflow", agency="TEST", id="DF_X", version="1.0"
    )
    dc = DataConstraint(
        id="CC_TEST",
        maintainer="TEST",
        version="1.0",
        name={"en": "Test Constraint"},
        constraint_type=ConstraintType.ALLOWED,
        attachment=ConstraintAttachment(dataflows=(Ref[Dataflow].unresolved(df_urn),)),
        regions=(CubeRegion(include=True, keys=(KeyValue(dimension_id="FREQ", values=frozenset({"A", "Q"})),)),),
    )
    put_data_constraint(cursor, dc)
    result = get_artefact(cursor, _urn(DataConstraint, "TEST", "CC_TEST"))
    assert result is not None
    assert result.id == "CC_TEST"
    assert result.agency_id == "TEST"
    assert result.name.get("en") == "Test Constraint"
    assert result.constraint_type is ConstraintType.ALLOWED
    assert len(result.attachment.dataflows) == 1
    assert result.attachment.dataflows[0].urn.id == "DF_X"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.include is True
    assert region.keys[0].dimension_id == "FREQ"
    assert region.keys[0].codes() == frozenset({"A", "Q"})


# ── CategoryScheme ───────────────────────────────────────────────────────────


def test_category_scheme_round_trip(cursor) -> None:
    cs = CategoryScheme(
        id="CS_TEST",
        maintainer="TEST",
        version="1.0",
        name={"en": "Test Categories"},
        categories=ItemList(
            [
                Category(
                    id="A",
                    name={"en": "Top A"},
                    categories=ItemList([Category(id="A1", name={"en": "Child A1"})]),
                ),
                Category(id="B", name={"en": "Top B"}),
            ]
        ),
    )
    put_category_scheme(cursor, cs)
    result = get_artefact(cursor, _urn(CategoryScheme, "TEST", "CS_TEST"))
    assert result is not None
    assert result.id == "CS_TEST"
    assert result.name.get("en") == "Test Categories"
    # Top-level categories preserved
    assert sorted(result.categories.ids()) == ["A", "B"]
    # Nested category survives
    a = result.categories["A"]
    assert sorted(a.categories.ids()) == ["A1"]
    assert a.categories["A1"].name.get("en") == "Child A1"


# ── Categorisation ───────────────────────────────────────────────────────────


def test_categorisation_round_trip(cursor) -> None:
    df_urn: SdmxUrn[Dataflow] = SdmxUrn(
        package="datastructure", artefact_class="Dataflow", agency="TEST", id="DF_X", version="1.0"
    )
    cat_urn: SdmxUrn[Category] = SdmxUrn(
        package="categoryscheme",
        artefact_class="Category",
        agency="TEST",
        id="CS_TEST",
        version="1.0",
        item_id="A",
    )
    cat = Categorisation(
        id="CAT_X",
        maintainer="TEST",
        version="1.0",
        name={"en": "DF_X under A"},
        source=Ref[Dataflow].unresolved(df_urn),
        target=Ref[Category].unresolved(cat_urn),
    )
    put_categorisation(cursor, cat)
    result = get_artefact(cursor, _urn(Categorisation, "TEST", "CAT_X"))
    assert result is not None
    assert result.id == "CAT_X"
    assert result.name.get("en") == "DF_X under A"
    assert result.source is not None
    assert result.source.urn.id == "DF_X"
    assert result.target is not None
    assert result.target.urn.id == "CS_TEST"
    assert result.target.urn.item_id == "A"


# ── HierarchyAssociation ─────────────────────────────────────────────────────


def test_hierarchy_association_round_trip(cursor) -> None:
    h_urn: SdmxUrn[Hierarchy] = SdmxUrn(
        package="codelist", artefact_class="Hierarchy", agency="TEST", id="H_GEO", version="1.0"
    )
    dim_urn: SdmxUrn[Dimension] = SdmxUrn(
        package="datastructure",
        artefact_class="Dimension",
        agency="TEST",
        id="DSD_X",
        version="1.0",
        item_id="REF_AREA",
    )
    df_urn: SdmxUrn[Dataflow] = SdmxUrn(
        package="datastructure", artefact_class="Dataflow", agency="TEST", id="DF_X", version="1.0"
    )
    ha = HierarchyAssociation(
        id="HA_GEO",
        maintainer="TEST",
        version="1.0",
        name={"en": "Default geo hierarchy for DF_X"},
        linked_hierarchy=Ref[Hierarchy].unresolved(h_urn),
        linked_object=Ref[Dimension].unresolved(dim_urn),
        context_object=Ref[Dataflow].unresolved(df_urn),
    )
    put_hierarchy_association(cursor, ha)
    result = get_artefact(cursor, _urn(HierarchyAssociation, "TEST", "HA_GEO"))
    assert result is not None
    assert result.id == "HA_GEO"
    assert result.name.get("en") == "Default geo hierarchy for DF_X"
    assert result.linked_hierarchy is not None
    assert result.linked_hierarchy.urn.id == "H_GEO"
    assert result.linked_object is not None
    assert result.linked_object.urn.item_id == "REF_AREA"
    assert result.context_object is not None
    assert result.context_object.urn.id == "DF_X"


def test_hierarchy_association_without_context_round_trip(cursor) -> None:
    h_urn: SdmxUrn[Hierarchy] = SdmxUrn(
        package="codelist", artefact_class="Hierarchy", agency="TEST", id="H_GEO", version="1.0"
    )
    dim_urn: SdmxUrn[Dimension] = SdmxUrn(
        package="datastructure",
        artefact_class="Dimension",
        agency="TEST",
        id="DSD_X",
        version="1.0",
        item_id="REF_AREA",
    )
    ha = HierarchyAssociation(
        name={"en": "HA_GLOBAL"},
        id="HA_GLOBAL",
        maintainer="TEST",
        version="1.0",
        linked_hierarchy=Ref[Hierarchy].unresolved(h_urn),
        linked_object=Ref[Dimension].unresolved(dim_urn),
        context_object=None,
    )
    put_hierarchy_association(cursor, ha)
    result = get_artefact(cursor, _urn(HierarchyAssociation, "TEST", "HA_GLOBAL"))
    assert result is not None
    assert result.context_object is None
    assert result.linked_hierarchy is not None
    assert result.linked_hierarchy.urn.id == "H_GEO"


# ── AgencyScheme ─────────────────────────────────────────────────────────────


def test_agency_scheme_round_trip(cursor) -> None:
    sc = AgencyScheme(
        id="AGENCIES",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Agency Scheme"},
        agencies=ItemList(
            [
                Agency(id="SDMX", name={"en": "SDMX"}),
                Agency(id="TEST", name={"en": "Test agency"}),
            ]
        ),
    )
    put_agency_scheme(cursor, sc)
    result = get_artefact(cursor, _urn(AgencyScheme, "SDMX", "AGENCIES"))
    assert result is not None
    assert result.id == "AGENCIES"
    assert sorted(result.agencies.ids()) == ["SDMX", "TEST"]
    assert result.agencies["TEST"].name.get("en") == "Test agency"


# ── DataProviderScheme ───────────────────────────────────────────────────────


def test_data_provider_scheme_round_trip(cursor) -> None:
    sc = DataProviderScheme(
        id="DATA_PROVIDERS",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Provider Scheme"},
        providers=ItemList(
            [
                DataProvider(id="ECB", name={"en": "European Central Bank"}),
                DataProvider(id="ESTAT", name={"en": "Eurostat"}),
            ]
        ),
    )
    put_data_provider_scheme(cursor, sc)
    result = get_artefact(cursor, _urn(DataProviderScheme, "SDMX", "DATA_PROVIDERS"))
    assert result is not None
    assert result.id == "DATA_PROVIDERS"
    assert sorted(result.providers.ids()) == ["ECB", "ESTAT"]
    assert result.providers["ECB"].name.get("en") == "European Central Bank"


# ── ProvisionAgreement ───────────────────────────────────────────────────────


def test_provision_agreement_round_trip(cursor) -> None:
    df_urn: SdmxUrn[Dataflow] = SdmxUrn(
        package="datastructure", artefact_class="Dataflow", agency="TEST", id="DF_X", version="1.0"
    )
    dp_urn: SdmxUrn[DataProvider] = SdmxUrn(
        package="base",
        artefact_class="DataProvider",
        agency="SDMX",
        id="DATA_PROVIDERS",
        version="1.0",
        item_id="ECB",
    )
    pa = ProvisionAgreement(
        id="PA_X",
        maintainer="TEST",
        version="1.0",
        name={"en": "ECB provides DF_X"},
        dataflow=Ref[Dataflow].unresolved(df_urn),
        data_provider=Ref[DataProvider].unresolved(dp_urn),
    )
    put_provision_agreement(cursor, pa)
    result = get_artefact(cursor, _urn(ProvisionAgreement, "TEST", "PA_X"))
    assert result is not None
    assert result.id == "PA_X"
    assert result.dataflow is not None
    assert result.dataflow.urn.id == "DF_X"
    assert result.data_provider is not None
    assert result.data_provider.urn.item_id == "ECB"


# ── RepresentationMap ────────────────────────────────────────────────────────


def test_representation_map_round_trip(cursor) -> None:
    src_urn: SdmxUrn[Codelist] = SdmxUrn(
        package="codelist", artefact_class="Codelist", agency="A", id="CL_SRC", version="1.0"
    )
    tgt_urn: SdmxUrn[Codelist] = SdmxUrn(
        package="codelist", artefact_class="Codelist", agency="A", id="CL_TGT", version="1.0"
    )
    rm = RepresentationMap(
        id="RM_TEST",
        maintainer="TEST",
        version="1.0",
        name={"en": "Source → Target"},
        sources=(Ref[Codelist].unresolved(src_urn),),
        targets=(Ref[Codelist].unresolved(tgt_urn),),
        mappings=(
            RepresentationMapping(sources=("A",), targets=("X",)),
            RepresentationMapping(sources=("B",), targets=("Y",)),
        ),
    )
    put_representation_map(cursor, rm)
    result = get_artefact(cursor, _urn(RepresentationMap, "TEST", "RM_TEST"))
    assert result is not None
    assert result.id == "RM_TEST"
    assert [e.urn.id for e in result.sources if isinstance(e, Ref)] == ["CL_SRC"]
    assert [e.urn.id for e in result.targets if isinstance(e, Ref)] == ["CL_TGT"]
    assert result.mappings == rm.mappings


# ── MetadataStructure ────────────────────────────────────────────────────────


def test_metadata_structure_round_trip(cursor) -> None:
    msd = MetadataStructure(
        id="MSD_TEST",
        maintainer="TEST",
        version="1.0",
        name={"en": "Test MSD"},
        attributes=ItemList(
            [
                MetadataAttribute(id="FOOTNOTE", concept=concept_ref("FOOTNOTE"), min_occurs=0, max_occurs=1),
                MetadataAttribute(
                    id="CONTACT",
                    concept=concept_ref("CONTACT"),
                    min_occurs=0,
                    max_occurs=None,
                    children=ItemList(
                        [MetadataAttribute(id="EMAIL", concept=concept_ref("EMAIL"), min_occurs=0, max_occurs=1)]
                    ),
                ),
            ]
        ),
    )
    put_metadata_structure(cursor, msd)
    result = get_artefact(cursor, _urn(MetadataStructure, "TEST", "MSD_TEST"))
    assert result is not None
    assert result.id == "MSD_TEST"
    assert sorted(result.attributes.ids()) == ["CONTACT", "FOOTNOTE"]
    # Nested child survives
    contact = result.attributes["CONTACT"]
    assert sorted(contact.children.ids()) == ["EMAIL"]
    assert contact.max_occurs is None


def test_metadata_attribute_facet_round_trips(cursor) -> None:
    """The `metadata_attribute` DOUBLE columns survive a round-trip.

    `dsd_component` and `concept` carry the same min/max/start/end value
    columns and are covered elsewhere (test_storage_facet_relationship,
    test_roundtrip_property_guard); this is the third table with them, and
    it had no facet coverage at all.
    """
    msd = MetadataStructure(
        id="MSD_FACET",
        maintainer="TEST",
        version="1.0",
        name={"en": "Facet MSD"},
        attributes=ItemList(
            [
                MetadataAttribute(
                    id="SCORE",
                    concept=concept_ref("SCORE"),
                    min_occurs=0,
                    max_occurs=1,
                    representation=Facet(
                        type="Integer",
                        min_value=0.0,
                        max_value=100.0,
                        # A sequence, as IM 3.6.3.3 requires of a start and an end (#482).
                        is_sequence=True,
                        interval=1.0,
                        start_value=1.0,
                        end_value=9.0,
                        decimals=2,
                    ),
                )
            ]
        ),
    )
    put_metadata_structure(cursor, msd)
    result = get_artefact(cursor, _urn(MetadataStructure, "TEST", "MSD_FACET"))
    assert result is not None
    rep = result.attributes["SCORE"].representation
    assert isinstance(rep, Facet)
    assert rep.min_value == 0.0
    assert rep.max_value == 100.0
    assert rep.start_value == 1.0
    assert rep.end_value == 9.0
    assert rep.decimals == 2
