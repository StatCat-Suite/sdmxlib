"""Tests for Phase 2 domain model types."""

from __future__ import annotations

import attrs
import pytest
from attrs import evolve

from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MeasureDimension,
    TimeDimension,
)
from sdmxlib.model.hierarchy import HierarchicalCodelist, Hierarchy
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.mapping import StructureSet
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import Agency, AgencyScheme, DataProvider, DataProviderScheme
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

# ── Helpers ───────────────────────────────────────────────────────────────


def _cl_urn(id: str = "CL_FREQ") -> SdmxUrn[Codelist]:
    return SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id=id,
        version="1.0",
    )


def _concept_urn(id: str = "FREQ") -> SdmxUrn[Concept]:
    return SdmxUrn.make(
        Concept,
        package="conceptscheme",
        artefact_class="Concept",
        agency="SDMX",
        id=id,
        version="1.0",
    )


def _make_codelist(id: str = "CL_FREQ", codes: list[str] | None = None) -> Codelist:
    code_list = [Code(c, name=InternationalString(en=c)) for c in (codes or ["A", "Q", "M"])]
    return Codelist(
        id=id,
        maintainer="SDMX",
        name=InternationalString(en=id),
        codes=ItemList(code_list),
    )


def _make_concept(id: str = "FREQ") -> Concept:
    return Concept(id=id, name=InternationalString(en=id))


def _make_dim(id: str = "FREQ", resolved: bool = True) -> Dimension:
    cl = _make_codelist()
    urn = _cl_urn()
    ref: Ref[Codelist] = Ref[Codelist].of(urn, cl) if resolved else Ref[Codelist].unresolved(urn)
    return Dimension(id=id, representation=ref, concept=concept_ref("X"))


def _make_dsd() -> DataStructure:
    freq_cl = _make_codelist("CL_FREQ", ["A", "Q", "M"])
    area_cl = _make_codelist("CL_AREA", ["US", "EU"])
    freq_concept = _make_concept("FREQ")

    freq_dim = Dimension(
        id="FREQ",
        representation=Ref[Codelist].of(_cl_urn("CL_FREQ"), freq_cl),
        concept=Ref[Concept].of(_concept_urn("FREQ"), freq_concept),
    )
    area_dim = Dimension(
        id="REF_AREA",
        representation=Ref[Codelist].of(_cl_urn("CL_AREA"), area_cl),
        concept=concept_ref("REF_AREA"),
    )
    text_dim = Dimension(
        id="INDICATOR",
        representation=Facet(type="String"),
        concept=concept_ref("INDICATOR"),
    )
    measure = Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))

    return DataStructure(
        id="CPI",
        maintainer="ESTAT",
        name=InternationalString(en="Consumer Price Index"),
        dimensions=ItemList([freq_dim, area_dim, text_dim]),
        measures=ItemList([measure]),
    )


# ── MaintainableArtefact.urn_for() ────────────────────────────────────────────


def test_codelist_urn_classmethod():
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    assert urn.id == "CL_FREQ"
    assert urn.agency == "SDMX"
    assert urn.package == "codelist"
    assert urn.artefact_class == "Codelist"


def test_datastructure_urn_classmethod():
    urn = DataStructure.urn_for(agency="ESTAT", id="CPI")
    assert urn.id == "CPI"
    assert urn.package == "datastructure"
    assert urn.artefact_class == "DataStructure"


def test_dataflow_urn_classmethod():
    urn = Dataflow.urn_for(agency="ESTAT", id="DF_CPI")
    assert urn.artefact_class == "Dataflow"


def test_provision_agreement_urn_classmethod():
    urn = ProvisionAgreement.urn_for(agency="ESTAT", id="PA_CPI")
    assert urn.artefact_class == "ProvisionAgreement"


# ── Code & Codelist ───────────────────────────────────────────────────────


def test_code_construction():
    code = Code(id="A", name=InternationalString(en="Annual"))
    assert code.id == "A"
    assert code.name["en"].text == "Annual"
    assert code.parent_id is None


def test_code_with_parent():
    code = Code(name={"en": "A1"}, id="A1", parent_id="A")
    assert code.parent_id == "A"


def test_codelist_construction():
    cl = _make_codelist()
    assert cl.id == "CL_FREQ"
    assert len(cl.codes) == 3
    assert cl.codes["A"].id == "A"


def test_codelist_roots():
    codes = ItemList(
        [Code("A", name={"en": "A"}), Code("A1", name={"en": "A1"}, parent_id="A"), Code("B", name={"en": "B"})]
    )
    cl = Codelist(name={"en": "CL_X"}, id="CL_X", maintainer="TEST", codes=codes)
    assert cl.roots().ids() == ["A", "B"]


def test_codelist_narrower():
    codes = ItemList(
        [
            Code("A", name={"en": "A"}),
            Code("A1", name={"en": "A1"}, parent_id="A"),
            Code("A2", name={"en": "A2"}, parent_id="A"),
            Code("B", name={"en": "B"}),
        ]
    )
    cl = Codelist(name={"en": "CL_X"}, id="CL_X", maintainer="TEST", codes=codes)
    assert cl.narrower("A").ids() == ["A1", "A2"]
    assert cl.narrower("B").ids() == []


def _geo_codelist() -> Codelist:
    """CA → ON, QC; ON → TOR, OTT; QC → MTL."""
    return Codelist(
        name={"en": "CL_GEO"},
        id="CL_GEO",
        maintainer="TEST",
        codes=[
            Code("CA", name={"en": "CA"}),
            Code("ON", name={"en": "ON"}, parent_id="CA"),
            Code("QC", name={"en": "QC"}, parent_id="CA"),
            Code("TOR", name={"en": "TOR"}, parent_id="ON"),
            Code("OTT", name={"en": "OTT"}, parent_id="ON"),
            Code("MTL", name={"en": "MTL"}, parent_id="QC"),
        ],
    )


def test_codelist_depths():
    cl = _geo_codelist()
    assert cl.depths() == {"CA": 0, "ON": 1, "QC": 1, "TOR": 2, "OTT": 2, "MTL": 2}


def test_codelist_broader_map():
    cl = _geo_codelist()
    assert cl.broader_map() == {
        "CA": None,
        "ON": "CA",
        "QC": "CA",
        "TOR": "ON",
        "OTT": "ON",
        "MTL": "QC",
    }


def test_codelist_narrower_map():
    cl = _geo_codelist()
    nm = cl.narrower_map()
    assert nm["CA"] == ("ON", "QC")
    assert nm["ON"] == ("TOR", "OTT")
    assert nm["QC"] == ("MTL",)
    assert nm["TOR"] == ()


def test_codelist_ancestors_map_root_first():
    cl = _geo_codelist()
    am = cl.ancestors_map()
    assert am["CA"] == ()
    assert am["ON"] == ("CA",)
    assert am["TOR"] == ("CA", "ON")  # root-first
    assert am["MTL"] == ("CA", "QC")


def test_codelist_descendants_map():
    cl = _geo_codelist()
    dm = cl.descendants_map()
    assert dm["CA"] == ("ON", "TOR", "OTT", "QC", "MTL")  # pre-order DFS
    assert dm["ON"] == ("TOR", "OTT")
    assert dm["TOR"] == ()


def test_codelist_inherit_along_broader():
    cl = _geo_codelist()
    # Anchor "country" at CA, "province" at ON. QC has no anchor →
    # MTL falls through QC to CA → "country"; TOR/OTT pick up "province" from ON.
    out = cl.inherit_along_broader({"CA": "country", "ON": "province"})
    assert out == {
        "CA": "country",
        "ON": "province",
        "QC": "country",
        "TOR": "province",
        "OTT": "province",
        "MTL": "country",
    }


def test_codelist_inherit_along_broader_omits_unmatched():
    cl = _geo_codelist()
    # Only TOR is anchored — TOR matches itself, no descendants in this fixture.
    # OTT/MTL/etc. have no value in their chain → omitted.
    out = cl.inherit_along_broader({"TOR": "city"})
    assert out == {"TOR": "city"}


def test_codelist_add_single():
    cl = _make_codelist()
    cl2 = cl.add(Code(id="W", name={"en": "Weekly"}))
    assert "W" in cl2.codes.ids()
    assert "W" not in cl.codes.ids()


def test_codelist_add_multiple():
    cl = _make_codelist()
    cl2 = cl.add([Code(id="W", name={"en": "Weekly"}), Code(id="D", name={"en": "Daily"})])
    assert "W" in cl2.codes.ids()
    assert "D" in cl2.codes.ids()
    assert len(cl2.codes.ids()) == len(cl.codes.ids()) + 2


def test_codelist_drop_single():
    cl = _make_codelist()
    first_id = cl.codes.ids()[0]
    cl2 = cl.drop(first_id)
    assert first_id not in cl2.codes.ids()
    assert first_id in cl.codes.ids()


def test_codelist_drop_multiple():
    cl = _make_codelist()
    ids = cl.codes.ids()[:2]
    cl2 = cl.drop(ids)
    for i in ids:
        assert i not in cl2.codes.ids()


def test_codelist_drop_missing_raises():
    cl = _make_codelist()
    with pytest.raises(KeyError):
        cl.drop("NO_SUCH_CODE")


# ── Concept & ConceptScheme ───────────────────────────────────────────────


def test_concept_construction():
    c = _make_concept()
    assert c.id == "FREQ"
    assert c.name["en"].text == "FREQ"
    assert c.core_representation is None


def test_concept_with_representation():
    c = Concept(name={"en": "X"}, id="X", core_representation=Facet(type="String"))
    assert isinstance(c.core_representation, Facet)


def test_concept_scheme_construction():
    cs = ConceptScheme(
        name={"en": "CS_COMMON"},
        id="CS_COMMON",
        maintainer="SDMX",
        concepts=ItemList([_make_concept("FREQ"), _make_concept("AREA")]),
    )
    assert len(cs.concepts) == 2
    assert cs.concepts["FREQ"].id == "FREQ"


def test_concept_scheme_urn():
    urn = ConceptScheme.urn_for(agency="SDMX", id="CS_COMMON")
    assert urn.artefact_class == "ConceptScheme"


def test_ref_to_returns_resolved_ref():
    cs = ConceptScheme(
        name={"en": "CS_COMMON"},
        id="CS_COMMON",
        maintainer="SDMX",
        version="1.0",
        concepts=ItemList([_make_concept("FREQ"), _make_concept("TIME_PERIOD")]),
    )
    ref = cs.ref_to("FREQ")
    assert ref.resolved
    assert ref().id == "FREQ"


def test_ref_to_item_urn():
    cs = ConceptScheme(
        name={"en": "CS_COMMON"},
        id="CS_COMMON",
        maintainer="SDMX",
        version="1.0",
        concepts=ItemList([_make_concept("FREQ")]),
    )
    ref = cs.ref_to("FREQ")
    assert ref.urn.item_id == "FREQ"
    assert ref.urn.id == "CS_COMMON"
    assert ref.urn.agency == "SDMX"
    assert ref.urn.version == "1.0"
    assert str(ref.urn) == ("urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=SDMX:CS_COMMON(1.0).FREQ")


def test_ref_to_raises_on_missing_concept():
    cs = ConceptScheme(
        name={"en": "CS_COMMON"},
        id="CS_COMMON",
        maintainer="SDMX",
        version="1.0",
        concepts=ItemList([_make_concept("FREQ")]),
    )
    with pytest.raises(KeyError):
        cs.ref_to("NONEXISTENT")


# ── Dimension ─────────────────────────────────────────────────────────────


def test_dimension_enumerated():
    dim = _make_dim(resolved=True)
    assert dim.is_enumerated
    assert isinstance(dim.representation, Ref)
    assert dim.representation.resolved


def test_dimension_codes_resolved():
    dim = _make_dim(resolved=True)
    assert dim.codes is not None
    assert "A" in dim.codes


def test_dimension_codes_unresolved():
    dim = _make_dim(resolved=False)
    assert dim.codes is None


def test_dimension_text_type():
    dim = Dimension(id="X", representation=Facet(type="String"), concept=concept_ref("X"))
    assert dim.text_type == "String"


def test_dimension_text_type_none_for_enumerated():
    dim = _make_dim()
    assert dim.text_type is None


def test_dimension_not_enumerated():
    dim = Dimension(id="X", representation=Facet(type="String"), concept=concept_ref("X"))
    assert not dim.is_enumerated


def test_dimension_enumeration_urn():
    dim = _make_dim()
    assert dim.enumeration_urn is not None
    assert dim.enumeration_urn.id == "CL_FREQ"


def test_dimension_pattern_match():
    dim = _make_dim()
    match dim:
        case Dimension(id=id_val, representation=rep) if rep is not None:
            assert id_val == "FREQ"
        case _:
            pytest.fail("Pattern match failed")


# ── TimeDimension ─────────────────────────────────────────────────────────


def test_time_dimension_defaults():
    """No format stated is the default every SDMX format gives one (#507)."""
    td = TimeDimension(concept=concept_ref("X"))
    assert td.id == "TIME_PERIOD"
    assert td.representation == Facet(type="ObservationalTimePeriod")
    assert td.text_type == "ObservationalTimePeriod"


def test_time_dimension_text_type():
    td = TimeDimension(representation=Facet(type="ObservationalTimePeriod"), concept=concept_ref("X"))
    assert td.text_type == "ObservationalTimePeriod"


# ── Attribute ─────────────────────────────────────────────────────────────


def test_attribute_construction():
    attr = Attribute(
        id="OBS_STATUS",
        usage="optional",
        attachment=AttachmentLevel.OBSERVATION,
        concept=concept_ref("OBS_STATUS"),
    )
    assert attr.attachment == AttachmentLevel.OBSERVATION
    assert not attr.is_enumerated


def test_attribute_enumerated():
    cl = _make_codelist("CL_OBS_STATUS")
    attr = Attribute(
        attachment=AttachmentLevel.DATASET,
        id="OBS_STATUS",
        representation=Ref[Codelist].of(_cl_urn("CL_OBS_STATUS"), cl),
        concept=concept_ref("OBS_STATUS"),
    )
    assert attr.is_enumerated
    assert attr.codes is not None


# ── MeasureDimension ─────────────────────────────────────────────────────


def test_measure_dimension_construction():
    md = MeasureDimension(id="INDICATOR", concept=concept_ref("INDICATOR"))
    assert md.id == "INDICATOR"
    assert not md.is_enumerated
    assert md.concepts is None


def test_measure_dimension_enumerated():
    """A MeasureDimension enumerates a ConceptScheme, not a Codelist."""
    cs = ConceptScheme(
        id="CS_INDICATOR",
        maintainer="T",
        version="1.0",
        name={"en": "Indicators"},
        concepts=ItemList([Concept(id="EMP", name={"en": "Employment"})]),
    )
    md = MeasureDimension(id="INDICATOR", representation=Ref.to(cs), concept=concept_ref("INDICATOR"))
    assert md.is_enumerated
    assert md.concepts is not None
    assert md.concept_scheme.id == "CS_INDICATOR"
    assert not hasattr(md, "codelist")
    assert not hasattr(md, "codes")


# ── Measure ───────────────────────────────────────────────────────────────


def test_measure_construction():
    m = Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))
    assert m.id == "OBS_VALUE"
    assert m.codes is None


# ── Group ─────────────────────────────────────────────────────────────────


def test_group_construction():
    g = Group(id="SiblingGroup", dimension_ids=["FREQ", "REF_AREA"])
    assert g.dimension_ids == ["FREQ", "REF_AREA"]


# ── DataStructure ─────────────────────────────────────────────────────────


def test_dsd_construction():
    dsd = _make_dsd()
    assert dsd.id == "CPI"
    assert len(dsd.dimensions) == 3
    assert len(dsd.measures) == 1


def test_dsd_dimensions_lookup():
    dsd = _make_dsd()
    assert dsd.dimensions["FREQ"].id == "FREQ"


def test_dsd_key_dimensions_no_time():
    dsd = _make_dsd()
    assert dsd.key_slot_ids("2.1") == ["FREQ", "REF_AREA", "INDICATOR"]
    assert dsd.key_slot_ids("3.0") == ["FREQ", "REF_AREA", "INDICATOR"], "no time dimension to add"
    assert [d.id for d in dsd.key_dimensions] == ["FREQ", "REF_AREA", "INDICATOR"]


def test_dsd_key_dimensions_with_time():
    dsd = _make_dsd()
    dsd_t = evolve(dsd, time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")))
    assert dsd_t.key_slot_ids("3.0") == ["FREQ", "REF_AREA", "INDICATOR", "TIME_PERIOD"]
    assert dsd_t.key_slot_ids("2.1") == ["FREQ", "REF_AREA", "INDICATOR"], "time is outside a 1.5 key"
    assert dsd_t.dimensions.ids() == ["FREQ", "REF_AREA", "INDICATOR"]


def test_dsd_enumerated_dimensions():
    dsd = _make_dsd()
    enumerated = dsd.enumerated_dimensions
    assert "FREQ" in enumerated
    assert "REF_AREA" in enumerated
    assert "INDICATOR" not in enumerated


def test_dsd_free_text_dimensions():
    dsd = _make_dsd()
    free = dsd.free_text_dimensions
    assert "INDICATOR" in free
    assert "FREQ" not in free


def test_dsd_all_codelists():
    dsd = _make_dsd()
    cls = dsd.all_codelists
    assert "FREQ" in cls
    assert "REF_AREA" in cls
    assert "INDICATOR" not in cls


def test_dsd_code_map():
    dsd = _make_dsd()
    cm = dsd.code_map(lang="en")
    assert "FREQ" in cm
    assert cm["FREQ"]["A"] == "A"


def test_dsd_column_labels_with_concept():
    dsd = _make_dsd()
    labels = dsd.column_labels(lang="en")
    assert labels["FREQ"] == "FREQ"
    assert labels["REF_AREA"] == "REF_AREA"


def test_dsd_components():
    dsd = _make_dsd()
    ids = dsd.components.ids()
    assert "FREQ" in ids
    assert "OBS_VALUE" in ids


def test_dsd_measure_dimension_none_by_default():
    dsd = _make_dsd()
    assert dsd.measure_dimension is None


def test_dsd_measure_dimension_stored():
    md = MeasureDimension(id="INDICATOR", concept=concept_ref("INDICATOR"))
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"))]),
        measure_dimension=md,
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    assert dsd.measure_dimension is md
    assert dsd.measure_dimension.id == "INDICATOR"


def test_dsd_components_includes_measure_dimension():
    md = MeasureDimension(id="INDICATOR", concept=concept_ref("INDICATOR"))
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"))]),
        measure_dimension=md,
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    ids = dsd.components.ids()
    assert "INDICATOR" in ids
    assert "REF_AREA" in ids
    assert "OBS_VALUE" in ids


def test_dsd_add_measure_dimension():
    dsd = _make_dsd()
    md = MeasureDimension(id="MEASURE", concept=concept_ref("MEASURE"))
    updated = dsd.add(md)
    assert updated.measure_dimension is md
    assert dsd.measure_dimension is None


def test_dsd_add_dimension():
    dsd = _make_dsd()
    new_dim = Dimension(id="COUNTRY", concept=concept_ref("COUNTRY"))
    updated = dsd.add(new_dim)
    assert "COUNTRY" in updated.dimensions.ids()
    assert "COUNTRY" not in dsd.dimensions.ids()
    assert len(list(updated.dimensions)) == len(list(dsd.dimensions)) + 1


def test_dsd_add_attribute():
    dsd = _make_dsd()
    new_attr = Attribute(attachment=AttachmentLevel.DATASET, id="DECIMALS", concept=concept_ref("DECIMALS"))
    updated = dsd.add(new_attr)
    assert "DECIMALS" in updated.attributes.ids()
    assert "DECIMALS" not in dsd.attributes.ids()


def test_dsd_add_measure():
    dsd = _make_dsd()
    new_meas = Measure(id="OBS_CONF", concept=concept_ref("OBS_CONF"))
    updated = dsd.add(new_meas)
    assert "OBS_CONF" in updated.measures.ids()
    assert "OBS_CONF" not in dsd.measures.ids()


def test_dsd_add_immutable():
    dsd = _make_dsd()
    updated = dsd.add(Dimension(id="COUNTRY", concept=concept_ref("COUNTRY")))
    assert updated is not dsd


def test_dsd_add_multiple():
    dsd = _make_dsd()
    updated = dsd.add(
        [
            Dimension(id="COUNTRY", concept=concept_ref("COUNTRY")),
            Attribute(attachment=AttachmentLevel.DATASET, id="DECIMALS", concept=concept_ref("DECIMALS")),
        ]
    )
    assert "COUNTRY" in updated.dimensions.ids()
    assert "DECIMALS" in updated.attributes.ids()


def test_dsd_drop_dimension():
    dsd = _make_dsd()
    updated = dsd.drop("FREQ")
    assert "FREQ" not in updated.dimensions.ids()
    assert "FREQ" in dsd.dimensions.ids()


def test_dsd_drop_multiple():
    dsd = _make_dsd()
    updated = dsd.drop(["FREQ", "REF_AREA"])
    assert "FREQ" not in updated.dimensions.ids()
    assert "REF_AREA" not in updated.dimensions.ids()
    assert "INDICATOR" in updated.dimensions.ids()


def test_dsd_drop_missing_raises():
    dsd = _make_dsd()
    with pytest.raises(KeyError):
        dsd.drop("NO_SUCH_ID")


# ── Dataflow ──────────────────────────────────────────────────────────────


def test_dataflow_construction():
    dsd = _make_dsd()
    dsd_urn = DataStructure.urn_for(agency="ESTAT", id="CPI")
    df = Dataflow(
        name={"en": "DF_CPI"},
        id="DF_CPI",
        maintainer="ESTAT",
        structure=Ref[DataStructure].of(dsd_urn, dsd),
    )
    assert df.structure is not None
    assert df.structure().id == "CPI"


def test_dataflow_no_structure():
    df = Dataflow(name={"en": "DF_CPI"}, id="DF_CPI", maintainer="ESTAT")
    assert df.structure is None


# ── ProvisionAgreement ────────────────────────────────────────────────────


def test_provision_agreement_construction():
    dsd = _make_dsd()
    dsd_urn = DataStructure.urn_for(agency="ESTAT", id="CPI")
    df = Dataflow(
        name={"en": "DF_CPI"},
        id="DF_CPI",
        maintainer="ESTAT",
        structure=Ref[DataStructure].of(dsd_urn, dsd),
    )
    df_urn = Dataflow.urn_for(agency="ESTAT", id="DF_CPI")
    pa = ProvisionAgreement(
        name={"en": "PA_CPI"},
        id="PA_CPI",
        maintainer="ESTAT",
        dataflow=Ref[Dataflow].of(df_urn, df),
    )
    assert pa.dataflow is not None
    assert pa.dataflow().id == "DF_CPI"
    assert pa.dataflow().structure().id == "CPI"  # type: ignore[union-attr]


# ── Navigation chain ──────────────────────────────────────────────────────


def test_navigation_chain_pa_to_codes():
    """pa.dataflow().structure().dimensions["FREQ"].codes works end-to-end."""
    dsd = _make_dsd()
    dsd_urn = DataStructure.urn_for(agency="ESTAT", id="CPI")
    df_urn = Dataflow.urn_for(agency="ESTAT", id="DF_CPI")

    df = Dataflow(name={"en": "DF_CPI"}, id="DF_CPI", maintainer="ESTAT", structure=Ref[DataStructure].of(dsd_urn, dsd))
    pa = ProvisionAgreement(
        name={"en": "PA_CPI"}, id="PA_CPI", maintainer="ESTAT", dataflow=Ref[Dataflow].of(df_urn, df)
    )

    codes = pa.dataflow().structure().dimensions["FREQ"].codes  # type: ignore[union-attr]
    assert codes is not None
    assert "A" in codes


# ── Category ──────────────────────────────────────────────────────────────


def test_category_construction():
    cat = Category(
        id="PRICES",
        name=InternationalString(en="Prices"),
        categories=ItemList([Category(name={"en": "CPI"}, id="CPI")]),
    )
    assert cat.id == "PRICES"
    assert len(cat.categories) == 1


def test_category_scheme_construction():
    cs = CategoryScheme(
        name={"en": "CS_TOPICS"},
        id="CS_TOPICS",
        maintainer="ESTAT",
        categories=ItemList([Category(name={"en": "PRICES"}, id="PRICES")]),
    )
    assert cs.categories["PRICES"].id == "PRICES"


def _topics_scheme() -> CategoryScheme:
    return CategoryScheme(
        name={"en": "CS_TOPICS"},
        id="CS_TOPICS",
        maintainer="TEST",
        categories=[
            Category(
                name={"en": "DEMO"},
                id="DEMO",
                categories=[
                    Category(
                        name={"en": "POP"},
                        id="POP",
                        categories=[
                            Category(name={"en": "POP_AGE"}, id="POP_AGE"),
                            Category(name={"en": "POP_SEX"}, id="POP_SEX"),
                        ],
                    ),
                    Category(name={"en": "HOUSE"}, id="HOUSE"),
                ],
            ),
            Category(name={"en": "ECON"}, id="ECON"),
        ],
    )


def test_category_scheme_skos_verbs():
    cs = _topics_scheme()
    assert [c.id for c in cs.roots()] == ["DEMO", "ECON"]
    assert {c.id for c in cs.leaves()} == {"POP_AGE", "POP_SEX", "HOUSE", "ECON"}
    assert [c.id for c in cs.narrower("DEMO")] == ["POP", "HOUSE"]
    parent = cs.broader("DEMO.POP.POP_AGE")
    assert parent is not None
    assert parent.id == "POP"
    assert cs.broader("DEMO") is None
    assert [c.id for c in cs.broader_transitive("DEMO.POP.POP_AGE")] == ["DEMO", "POP"]
    assert {c.id for c in cs.narrower_transitive("DEMO")} == {"POP", "POP_AGE", "POP_SEX", "HOUSE"}
    assert cs.depth_of("DEMO.POP.POP_AGE") == 2
    assert cs["DEMO.POP.POP_AGE"].id == "POP_AGE"


def test_category_scheme_bulk_maps():
    cs = _topics_scheme()
    assert cs.depths() == {
        "DEMO": 0,
        "ECON": 0,
        "DEMO.POP": 1,
        "DEMO.HOUSE": 1,
        "DEMO.POP.POP_AGE": 2,
        "DEMO.POP.POP_SEX": 2,
    }
    assert cs.broader_map()["DEMO.POP.POP_AGE"] == "DEMO.POP"
    assert cs.narrower_map()["DEMO"] == ("DEMO.POP", "DEMO.HOUSE")
    assert cs.ancestors_map()["DEMO.POP.POP_AGE"] == ("DEMO", "DEMO.POP")
    assert cs.descendants_map()["DEMO"] == ("DEMO.POP", "DEMO.POP.POP_AGE", "DEMO.POP.POP_SEX", "DEMO.HOUSE")
    assert cs.inherit_along_broader({"DEMO": "demographics"}) == {
        "DEMO": "demographics",
        "DEMO.POP": "demographics",
        "DEMO.HOUSE": "demographics",
        "DEMO.POP.POP_AGE": "demographics",
        "DEMO.POP.POP_SEX": "demographics",
    }


def test_categorisation_construction():
    cat = Category(name={"en": "PRICES"}, id="PRICES")
    cat_urn = SdmxUrn.make(
        Category,
        package="categoryscheme",
        artefact_class="Category",
        agency="ESTAT",
        id="PRICES",
    )
    ca = Categorisation(
        name={"en": "CA_CPI"},
        id="CA_CPI",
        maintainer="ESTAT",
        target=Ref[Category].of(cat_urn, cat),
    )
    assert ca.target is not None
    resolved = ca.target()
    assert isinstance(resolved, Category)
    assert resolved.id == "PRICES"


# ── Organisation ──────────────────────────────────────────────────────────


def test_agency_construction():
    a = Agency(id="ESTAT", name=InternationalString(en="Eurostat"))
    assert a.id == "ESTAT"
    assert a.name["en"].text == "Eurostat"


def test_data_provider_construction():
    dp = DataProvider(id="ECB", name=InternationalString(en="European Central Bank"))
    assert dp.id == "ECB"


# ── StructureMessage ──────────────────────────────────────────────────────


def test_structure_message_empty():
    msg = StructureMessage()
    assert len(msg.codelists) == 0
    assert msg.all_artefacts() == []


def test_structure_message_with_artefacts():
    cl = _make_codelist()
    msg = StructureMessage(codelists=ArtefactList([cl]))
    assert len(msg.codelists) == 1
    assert msg.codelists["CL_FREQ"].id == "CL_FREQ"
    assert len(msg.all_artefacts()) == 1


def test_structure_message_lookup_by_qualified_key():
    cl = _make_codelist()
    msg = StructureMessage(codelists=ArtefactList([cl]))
    assert msg.codelists["SDMX:CL_FREQ"].id == "CL_FREQ"


# ── Representation pattern matching on components ─────────────────────────


def test_representation_pattern_match_on_dimension():
    enum_dim = Dimension(id="FREQ", representation=Ref[Codelist].unresolved(_cl_urn()), concept=concept_ref("FREQ"))
    text_dim = Dimension(id="X", representation=Facet(type="String"), concept=concept_ref("X"))
    none_dim = Dimension(id="Y", concept=concept_ref("Y"))

    for dim, expected in [(enum_dim, "enum"), (text_dim, "text"), (none_dim, "none")]:
        match dim.representation:
            case Ref():
                result = "enum"
            case Facet():
                result = "text"
            case None:
                result = "none"
        assert result == expected


# ── __post_init__ coercion tests ──────────────────────────────────────────────


def test_codelist_name_dict_coerced_to_istring():
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", name={"en": "Frequency"})
    assert isinstance(cl.name, InternationalString)
    assert cl.name["en"].text == "Frequency"


def test_codelist_codes_list_coerced_to_itemlist():
    cl = Codelist(
        name={"en": "CL_FREQ"},
        id="CL_FREQ",
        maintainer="SDMX",
        codes=[Code(name={"en": "A"}, id="A"), Code(name={"en": "Q"}, id="Q")],
    )
    assert isinstance(cl.codes, ItemList)
    assert cl.codes["A"].id == "A"


def test_concept_name_dict_coerced_to_istring():
    c = Concept(id="FREQ", name={"en": "Frequency", "fr": "Fréquence"})
    assert isinstance(c.name, InternationalString)
    assert c.name["fr"].text == "Fréquence"


def test_concept_scheme_concepts_list_coerced_to_itemlist():
    cs = ConceptScheme(
        name={"en": "CS_COMMON"},
        id="CS_COMMON",
        maintainer="SDMX",
        concepts=[Concept(name={"en": "FREQ"}, id="FREQ"), Concept(name={"en": "REF_AREA"}, id="REF_AREA")],
    )
    assert isinstance(cs.concepts, ItemList)
    assert cs.concepts["FREQ"].id == "FREQ"


def test_annotations_list_coerced():
    cl = Codelist(
        name={"en": "CL_FREQ"},
        id="CL_FREQ",
        maintainer="SDMX",
        annotations=[Annotation(id="NOTE", text=InternationalString.of({"en": "hi"}))],
    )
    assert isinstance(cl.annotations, Annotations)


def test_dimension_codelist_instance_coerced_to_ref():
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX")
    dim = Dimension(id="FREQ", representation=cl, concept=concept_ref("FREQ"))
    assert isinstance(dim.representation, Ref)
    assert dim.representation.resolved
    assert isinstance(dim.representation, Ref)
    assert dim.representation() is cl


def test_attribute_codelist_instance_coerced_to_ref():
    cl = Codelist(name={"en": "CL_CONF"}, id="CL_CONF", maintainer="SDMX")
    attr = Attribute(
        attachment=AttachmentLevel.DATASET, id="CONF_STATUS", representation=cl, concept=concept_ref("CONF_STATUS")
    )
    assert isinstance(attr.representation, Ref)
    assert attr.representation() is cl


def test_measure_codelist_instance_coerced_to_ref():
    cl = Codelist(name={"en": "CL_UNIT"}, id="CL_UNIT", maintainer="SDMX")
    meas = Measure(id="OBS_VALUE", representation=cl, concept=concept_ref("OBS_VALUE"))
    assert isinstance(meas.representation, Ref)
    assert meas.representation() is cl


def test_datastructure_dimensions_list_coerced_to_itemlist():
    dsd = DataStructure(
        name={"en": "DSD_TEST"},
        id="DSD_TEST",
        maintainer="TEST",
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
    )
    assert isinstance(dsd.dimensions, ItemList)
    assert dsd.dimensions["FREQ"].id == "FREQ"


def test_dataflow_structure_instance_coerced_to_ref():
    dsd = DataStructure(name={"en": "DSD_TEST"}, id="DSD_TEST", maintainer="TEST")
    df = Dataflow(name={"en": "DF_TEST"}, id="DF_TEST", maintainer="TEST", structure=dsd)
    assert isinstance(df.structure, Ref)
    assert df.structure() is dsd


def test_structure_message_codelists_list_coerced_to_artefactlist():
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX")
    msg = StructureMessage(codelists=[cl])
    assert isinstance(msg.codelists, ArtefactList)
    assert len(list(msg.codelists)) == 1


def test_end_to_end_construction_from_plain_types():
    """Spec §4: construct a full DSD from plain Python types without Ref or ItemList."""
    cl = Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        name={"en": "Frequency"},
        codes=[Code(id="A", name={"en": "Annual"}), Code(id="Q", name={"en": "Quarterly"})],
    )
    dsd = DataStructure(
        id="DSD_EXAMPLE",
        maintainer="TEST",
        name={"en": "Example DSD"},
        dimensions=[Dimension(id="FREQ", representation=cl, concept=concept_ref("FREQ"))],
    )
    msg = StructureMessage(
        codelists=[cl],
        data_structures=[dsd],
    )

    # Coercions happened
    assert isinstance(dsd.name, InternationalString)
    assert isinstance(dsd.dimensions, ItemList)
    freq_dim = dsd.dimensions["FREQ"]
    assert isinstance(freq_dim.representation, Ref)
    assert freq_dim.is_enumerated
    assert isinstance(msg.codelists, ArtefactList)
    assert isinstance(msg.data_structures, ArtefactList)


# ── add() / drop() on scheme types ───────────────────────────────────────────


def test_concept_scheme_add():
    cs = ConceptScheme(name={"en": "CS"}, id="CS", maintainer="TEST", concepts=[Concept(name={"en": "A"}, id="A")])
    cs2 = cs.add(Concept(name={"en": "B"}, id="B"))
    assert cs2.concepts.ids() == ["A", "B"]
    assert cs.concepts.ids() == ["A"]  # original unchanged


def test_concept_scheme_add_multiple():
    cs = ConceptScheme(name={"en": "CS"}, id="CS", maintainer="TEST", concepts=[Concept(name={"en": "A"}, id="A")])
    cs2 = cs.add([Concept(name={"en": "B"}, id="B"), Concept(name={"en": "C"}, id="C")])
    assert cs2.concepts.ids() == ["A", "B", "C"]


def test_concept_scheme_drop():
    cs = ConceptScheme(
        name={"en": "CS"},
        id="CS",
        maintainer="TEST",
        concepts=[Concept(name={"en": "A"}, id="A"), Concept(name={"en": "B"}, id="B")],
    )
    cs2 = cs.drop("A")
    assert cs2.concepts.ids() == ["B"]
    assert cs.concepts.ids() == ["A", "B"]


def test_concept_scheme_drop_multiple():
    cs = ConceptScheme(
        name={"en": "CS"},
        id="CS",
        maintainer="TEST",
        concepts=[
            Concept(name={"en": "A"}, id="A"),
            Concept(name={"en": "B"}, id="B"),
            Concept(name={"en": "C"}, id="C"),
        ],
    )
    cs2 = cs.drop(["A", "C"])
    assert cs2.concepts.ids() == ["B"]


def test_concept_scheme_drop_missing_raises():
    cs = ConceptScheme(name={"en": "CS"}, id="CS", maintainer="TEST", concepts=[Concept(name={"en": "A"}, id="A")])
    with pytest.raises(KeyError):
        cs.drop("NO_SUCH")


def test_category_scheme_add():
    scheme = CategoryScheme(
        name={"en": "CS"}, id="CS", maintainer="TEST", categories=[Category(name={"en": "A"}, id="A")]
    )
    scheme2 = scheme.add(Category(name={"en": "B"}, id="B"))
    assert scheme2.categories.ids() == ["A", "B"]
    assert scheme.categories.ids() == ["A"]


def test_category_scheme_drop():
    scheme = CategoryScheme(
        name={"en": "CS"},
        id="CS",
        maintainer="TEST",
        categories=[Category(name={"en": "A"}, id="A"), Category(name={"en": "B"}, id="B")],
    )
    scheme2 = scheme.drop("A")
    assert scheme2.categories.ids() == ["B"]


def test_agency_scheme_add():
    scheme = AgencyScheme(
        name={"en": "AGENCIES"}, id="AGENCIES", maintainer="SDMX", agencies=[Agency(id="SDMX", name={"en": "SDMX"})]
    )
    scheme2 = scheme.add(Agency(id="ECB", name={"en": "European Central Bank"}))
    assert scheme2.agencies.ids() == ["SDMX", "ECB"]
    assert scheme.agencies.ids() == ["SDMX"]  # original unchanged


def test_agency_scheme_drop():
    scheme = AgencyScheme(
        name={"en": "AGENCIES"},
        id="AGENCIES",
        maintainer="SDMX",
        agencies=[Agency(id="SDMX", name={"en": "SDMX"}), Agency(id="ECB", name={"en": "ECB"})],
    )
    scheme2 = scheme.drop("ECB")
    assert scheme2.agencies.ids() == ["SDMX"]


def test_data_provider_scheme_add():
    scheme = DataProviderScheme(
        name={"en": "DPS"},
        id="DATA_PROVIDERS",
        maintainer="ECB",
        providers=[DataProvider(name={"en": "ECB"}, id="ECB")],
    )
    scheme2 = scheme.add(DataProvider(name={"en": "BIS"}, id="BIS"))
    assert scheme2.providers.ids() == ["ECB", "BIS"]
    assert scheme.providers.ids() == ["ECB"]


def test_data_provider_scheme_drop():
    scheme = DataProviderScheme(
        name={"en": "DPS"},
        id="DATA_PROVIDERS",
        maintainer="ECB",
        providers=[DataProvider(name={"en": "ECB"}, id="ECB"), DataProvider(name={"en": "BIS"}, id="BIS")],
    )
    scheme2 = scheme.drop("BIS")
    assert scheme2.providers.ids() == ["ECB"]


# ── SdmxUrn coercion tests ────────────────────────────────────────────────────


def test_dimension_representation_urn_coerced_to_ref():
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    dim = Dimension(id="FREQ", representation=urn, concept=concept_ref("FREQ"))
    assert isinstance(dim.representation, Ref)
    assert not dim.representation.resolved
    assert dim.representation.id == "CL_FREQ"
    assert dim.representation.agency == "SDMX"
    assert dim.is_enumerated


def test_dimension_concept_urn_coerced_to_ref():
    urn = Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ")
    dim = Dimension(id="FREQ", concept=urn)
    assert isinstance(dim.concept, Ref)
    assert not dim.concept.resolved
    assert dim.concept.item_id == "FREQ"


def test_attribute_representation_urn_coerced_to_ref():
    urn = Codelist.urn_for(agency="SDMX", id="CL_CONF", version="1.0")
    attr = Attribute(
        attachment=AttachmentLevel.DATASET, id="CONF_STATUS", representation=urn, concept=concept_ref("CONF_STATUS")
    )
    assert isinstance(attr.representation, Ref)
    assert not attr.representation.resolved
    assert attr.representation.id == "CL_CONF"


def test_attribute_concept_urn_coerced_to_ref():
    urn = Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="OBS_STATUS")
    attr = Attribute(attachment=AttachmentLevel.DATASET, id="OBS_STATUS", concept=urn)
    assert isinstance(attr.concept, Ref)
    assert not attr.concept.resolved
    assert attr.concept.item_id == "OBS_STATUS"


def test_measure_representation_urn_coerced_to_ref():
    urn = Codelist.urn_for(agency="SDMX", id="CL_UNIT", version="1.0")
    meas = Measure(id="OBS_VALUE", representation=urn, concept=concept_ref("OBS_VALUE"))
    assert isinstance(meas.representation, Ref)
    assert not meas.representation.resolved
    assert meas.representation.id == "CL_UNIT"


def test_measure_concept_urn_coerced_to_ref():
    urn = Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="OBS_VALUE")
    meas = Measure(id="OBS_VALUE", concept=urn)
    assert isinstance(meas.concept, Ref)
    assert not meas.concept.resolved
    assert meas.concept.item_id == "OBS_VALUE"


def test_dataflow_structure_urn_coerced_to_ref():
    urn = DataStructure.urn_for(agency="TEST", id="DSD_TEST", version="1.0")
    df = Dataflow(name={"en": "DF_TEST"}, id="DF_TEST", maintainer="TEST", structure=urn)
    assert isinstance(df.structure, Ref)
    assert not df.structure.resolved
    assert df.structure.id == "DSD_TEST"


def test_dimension_both_urns_coerced():
    """Full ergonomic example: both representation and concept as bare SdmxUrn."""
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    concept_urn = Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ")
    dim = Dimension(id="FREQ", representation=cl_urn, concept=concept_urn)
    assert isinstance(dim.representation, Ref)
    assert isinstance(dim.concept, Ref)
    assert dim.representation.id == "CL_FREQ"
    assert dim.concept.item_id == "FREQ"
    assert dim.is_enumerated


# ── .urn is a property; urn_for builds ────────────────────────────────────


def test_an_artefact_reports_its_own_urn() -> None:
    """`urn` used to be the classmethod builder, so this returned a bound
    method — and `ref.py`'s own docstring offered a call that raised."""
    codelist = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", name={"en": "f"})
    assert str(codelist.urn) == "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"


def test_the_documented_ref_spelling_works() -> None:
    """The example in `ref.py`'s `SupportsUrn` docstring, executed.

    It was written as though `.urn` were a property, and was not. Both
    spellings must now give the same reference.
    """
    codelist = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", name={"en": "f"})
    assert str(Ref.unresolved(codelist.urn).urn) == str(Ref.to(codelist).urn)


def test_the_builder_and_the_property_differ_on_version_default() -> None:
    """Why they are two names rather than one.

    An identity you look *up* defaults to `latest`; an artefact you hold has
    the version it has. One function cannot default both ways.
    """
    assert str(Codelist.urn_for(agency="SDMX", id="CL_FREQ")).endswith("CL_FREQ")
    assert str(Codelist(id="CL_FREQ", maintainer="SDMX", name={"en": "f"}).urn).endswith("CL_FREQ(1.0)")


def test_the_property_recovers_the_type_where_artefact_urn_cannot() -> None:
    """`artefact_urn` takes a `SupportsUrn` and returns `SdmxUrn[object]`; the
    property has `type(self)` and returns `SdmxUrn[Self]`. Same URN, and only
    one of them tells a consumer what it names (#399)."""
    from sdmxlib.model.ref import artefact_urn

    codelist = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", name={"en": "f"})
    assert str(codelist.urn) == str(artefact_urn(codelist))


def test_an_item_has_no_urn_of_its_own() -> None:
    """Items are identified within their scheme, not independently — so the
    property is on `MaintainableArtefact` and a `Code` does not get one."""
    assert not hasattr(Code(id="A", name={"en": "a"}), "urn")


# ── all_artefacts() is every slot, and stays that way (#394) ──────────────


def test_all_artefacts_covers_every_slot():
    """The contract, compared against the class rather than restated.

    `structure_sets` and `hierarchical_codelists` were missing, and the two
    consequences pulled opposite ways: `validate_references` never checked
    either type, while `parse(xml, registry=…)` **silently dropped** them —
    a `HierarchicalCodelist` named "Geographic hierarchy" ceased to exist on
    ingest, which is the name #239 added the container to stop losing.

    Written as a comparison so a *new* slot cannot be added without a
    decision. The same shape `test_the_plan_holds_the_question_and_the_query_holds_the_binding`
    uses for `Plan`, and for the same reason: a list that has to be remembered
    is a list that drifts.
    """
    import inspect
    import re

    slots = {f.name for f in attrs.fields(StructureMessage) if f.name != "header" and not f.name.startswith("_")}
    listed = set(re.findall(r"self\.(\w+)", inspect.getsource(StructureMessage.all_artefacts)))
    explained = {slot for slot in slots - listed if f"`{slot}`" in (StructureMessage.all_artefacts.__doc__ or "")}
    assert slots - listed - explained == set(), (
        f"all_artefacts() does not yield {sorted(slots - listed - explained)} — either add them, or "
        f"say in the docstring why the message carries an artefact the method hides"
    )


def test_all_artefacts_yields_the_two_that_were_missing():
    """The behavioural half of the test above, since a regex over source is a
    proxy and this is the thing itself."""
    hierarchy = Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"})
    container = HierarchicalCodelist(
        id="HCL",
        maintainer="AG",
        version="1.0",
        name={"en": "Geographic hierarchy"},
        hierarchies=(Ref.to(hierarchy),),
    )
    structure_set = StructureSet(id="SS", maintainer="AG", version="1.0", name={"en": "ss"})
    msg = StructureMessage(hierarchies=[hierarchy], hierarchical_codelists=[container], structure_sets=[structure_set])
    assert {type(a).__name__ for a in msg.all_artefacts()} == {
        "Hierarchy",
        "HierarchicalCodelist",
        "StructureSet",
    }
