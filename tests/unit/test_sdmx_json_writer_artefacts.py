"""SDMX-JSON writer coverage for artefact types not exercised by the
existing fixture-driven round-trip tests. Closes #123.

The two contiguous uncovered ranges in ``formats/sdmx_json/writer.py``
were ``_write_provision_agreement`` / ``_write_constraint_attachment``
(lines 425–454) and ``_write_hierarchical_code`` / ``_write_hierarchy``
(lines 602–646). These tests construct each artefact directly, serialise
through the JSON writer, parse the result back, and assert the surviving
state matches.
"""

import pytest

from sdmxlib.formats.sdmx_json.writer import write as write_json
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    InclusionMode,
    MemberValue,
)
from sdmxlib.model.collections import ItemList
from sdmxlib.model.constraint import (
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    KeyValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    Contact,
    DataProvider,
    DataProviderScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import code_ref
from tests.support.roundtrip import roundtrip_json

pytestmark = pytest.mark.unit


# ── ProvisionAgreement ───────────────────────────────────────────────────────


def test_provision_agreement_json_round_trip() -> None:
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
        description={"en": "Optional description for coverage"},
        dataflow=Ref[Dataflow].unresolved(df_urn),
        data_provider=Ref[DataProvider].unresolved(dp_urn),
    )
    rt = roundtrip_json(StructureMessage(provision_agreements=[pa]))
    assert rt.provision_agreements.ids() == ["PA_X"]
    rt_pa = rt.provision_agreements["PA_X"]
    assert rt_pa.name.get("en") == "ECB provides DF_X"
    assert rt_pa.description.get("en") == "Optional description for coverage"
    assert rt_pa.dataflow is not None
    assert rt_pa.dataflow.urn.id == "DF_X"
    assert rt_pa.data_provider is not None
    assert rt_pa.data_provider.urn.item_id == "ECB"


# ── ConstraintAttachment variants ────────────────────────────────────────────


def _build_constraint(attachment: ConstraintAttachment) -> DataConstraint:
    return DataConstraint(
        id="CC_TEST",
        maintainer="TEST",
        version="1.0",
        name={"en": "Test Constraint"},
        constraint_type=ConstraintType.ALLOWED,
        attachment=attachment,
        regions=(CubeRegion(include=True, keys=(KeyValue(dimension_id="FREQ", values=frozenset({"A"})),)),),
    )


def test_constraint_attachment_dataflow_json_round_trip() -> None:
    df_urn: SdmxUrn[Dataflow] = SdmxUrn(
        package="datastructure", artefact_class="Dataflow", agency="TEST", id="DF_X", version="1.0"
    )
    cc = _build_constraint(ConstraintAttachment(dataflows=(Ref[Dataflow].unresolved(df_urn),)))
    rt = roundtrip_json(StructureMessage(constraints=[cc]))
    rt_cc = rt.constraints["CC_TEST"]
    assert len(rt_cc.attachment.dataflows) == 1
    assert rt_cc.attachment.dataflows[0].urn.id == "DF_X"
    assert rt_cc.attachment.data_structures == ()
    assert rt_cc.attachment.provision_agreements == ()


def test_constraint_attachment_data_structure_json_round_trip() -> None:
    ds_urn: SdmxUrn[DataStructure] = SdmxUrn(
        package="datastructure", artefact_class="DataStructure", agency="TEST", id="DSD_X", version="1.0"
    )
    cc = _build_constraint(ConstraintAttachment(data_structures=(Ref[DataStructure].unresolved(ds_urn),)))
    rt = roundtrip_json(StructureMessage(constraints=[cc]))
    rt_cc = rt.constraints["CC_TEST"]
    assert len(rt_cc.attachment.data_structures) == 1
    assert rt_cc.attachment.data_structures[0].urn.id == "DSD_X"
    assert rt_cc.attachment.dataflows == ()


def test_constraint_attachment_provision_agreement_json_round_trip() -> None:
    pa_urn: SdmxUrn[ProvisionAgreement] = SdmxUrn(
        package="registry", artefact_class="ProvisionAgreement", agency="TEST", id="PA_X", version="1.0"
    )
    cc = _build_constraint(ConstraintAttachment(provision_agreements=(Ref[ProvisionAgreement].unresolved(pa_urn),)))
    rt = roundtrip_json(StructureMessage(constraints=[cc]))
    rt_cc = rt.constraints["CC_TEST"]
    assert len(rt_cc.attachment.provision_agreements) == 1
    assert rt_cc.attachment.provision_agreements[0].urn.id == "PA_X"
    assert rt_cc.attachment.dataflows == ()


# ── Hierarchy + HierarchicalCode ─────────────────────────────────────────────


def test_hierarchy_with_levels_json_round_trip() -> None:
    """3.1-shaped Hierarchy with formal Levels and a multi-level tree."""
    continent = Level(id="Continent", name={"en": "Continent"})
    country = Level(id="Country", name={"en": "Country"})
    h = Hierarchy(
        id="H_GEO",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Geography"},
        description={"en": "Multi-level test hierarchy"},
        has_formal_levels=True,
        levels=[continent, country],
        tree=[
            HierarchicalCode(
                id="EUROPE",
                level=continent,
                children=ItemList(
                    [
                        HierarchicalCode(id="FR", level=country, code=code_ref("FR")),
                        HierarchicalCode(id="DE", level=country, code=code_ref("DE")),
                    ]
                ),
                code=code_ref("EUROPE"),
            ),
        ],
    )
    rt = roundtrip_json(StructureMessage(hierarchies=[h]))
    assert rt.hierarchies.ids() == ["H_GEO"]
    rt_h = rt.hierarchies["H_GEO"]
    assert rt_h.name.get("en") == "Geography"
    assert rt_h.description.get("en") == "Multi-level test hierarchy"
    assert sorted(lv.id for lv in rt_h.levels) == ["Continent", "Country"]
    # Tree roots and nested children survive
    assert rt_h.tree.ids() == ["EUROPE"]
    europe = rt_h.tree["EUROPE"]
    assert sorted(europe.children.ids()) == ["DE", "FR"]
    # Per-node level reference resolved
    fr = europe.children["FR"]
    assert fr.level is not None
    assert fr.level.id == "Country"


def test_agency_scheme_all_optional_fields_json_round_trip() -> None:
    """AgencyScheme with description / isFinal / valid_from / valid_to / contacts —
    hits the optional-field branches in _write_agency_scheme / _write_agency /
    _write_contact."""
    from datetime import datetime

    contact = Contact(
        name={"en": "Support"},
        department={"en": "IT"},
        role={"en": "Maintainer"},
        phones=("+1 555 0100",),
        faxes=("+1 555 0101",),
        x400s=("/C=US/A=ATTMAIL/PN=SUPPORT",),
        uris=("https://example.com/support",),
        emails=("support@example.com",),
    )
    agency = Agency(
        id="ACME",
        name={"en": "Acme Corp"},
        description={"en": "Maintainer agency"},
        contacts=(contact,),
    )
    scheme = AgencyScheme(
        id="AGENCIES",
        maintainer="ACME",
        version="1.0",
        name={"en": "Agencies"},
        description={"en": "All agencies"},
        valid_from=datetime(2020, 1, 1),
        valid_to=datetime(2030, 12, 31),
        agencies=ItemList([agency]),
    )
    rt = roundtrip_json(StructureMessage(agency_schemes=[scheme]))
    rt_scheme = rt.agency_schemes["AGENCIES"]
    assert rt_scheme.description.get("en") == "All agencies"
    assert rt_scheme.valid_from is not None
    assert rt_scheme.valid_to is not None
    rt_agency = rt_scheme.agencies["ACME"]
    assert rt_agency.description.get("en") == "Maintainer agency"
    rt_contact = rt_agency.contacts[0]
    assert rt_contact.department.get("en") == "IT"
    assert rt_contact.role.get("en") == "Maintainer"
    assert rt_contact.phones == ("+1 555 0100",)
    assert rt_contact.emails == ("support@example.com",)


def test_data_provider_scheme_all_optional_fields_json_round_trip() -> None:
    """Mirror coverage for DataProviderScheme + DataProvider optional fields."""
    from datetime import datetime

    contact = Contact(
        name={"en": "Data team"},
        department={"en": "Statistics"},
        phones=("+1 555 0200",),
        emails=("data@example.com",),
    )
    provider = DataProvider(
        id="ECB",
        name={"en": "European Central Bank"},
        description={"en": "Eurozone central bank"},
        contacts=(contact,),
    )
    scheme = DataProviderScheme(
        id="DATA_PROVIDERS",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Providers"},
        description={"en": "All data providers"},
        valid_from=datetime(2020, 1, 1),
        valid_to=datetime(2030, 12, 31),
        providers=ItemList([provider]),
    )
    rt = roundtrip_json(StructureMessage(data_provider_schemes=[scheme]))
    rt_scheme = rt.data_provider_schemes["DATA_PROVIDERS"]
    assert rt_scheme.description.get("en") == "All data providers"
    rt_provider = rt_scheme.providers["ECB"]
    assert rt_provider.description.get("en") == "Eurozone central bank"
    assert rt_provider.contacts[0].department.get("en") == "Statistics"


def test_two_hierarchies_over_one_codelist_json_round_trip() -> None:
    """Two distinct Hierarchy artefacts referencing codes from the same Codelist
    survive a JSON round-trip with their distinct parent graphs intact (#135)."""
    from sdmxlib.model.codelist import Code

    def _code_urn(cid: str) -> SdmxUrn[Code]:
        return SdmxUrn(
            package="codelist",
            artefact_class="Code",
            agency="TEST",
            id="CL_AREA",
            version="1.0",
            item_id=cid,
        )

    def _hcode(cid: str, children: list[HierarchicalCode] | None = None) -> HierarchicalCode:
        return HierarchicalCode(
            id=cid,
            code=Ref[Code].unresolved(_code_urn(cid)),
            children=children or [],
        )

    # Admin: TOR sits under ON. Statistical: TOR sits directly under CA.
    h_admin = Hierarchy(
        id="H_GEO_ADMIN",
        maintainer="TEST",
        version="1.0",
        name={"en": "Admin"},
        tree=[_hcode("CA", [_hcode("ON", [_hcode("TOR")]), _hcode("QC")])],
    )
    h_stat = Hierarchy(
        id="H_GEO_STAT",
        maintainer="TEST",
        version="1.0",
        name={"en": "Statistical"},
        tree=[_hcode("CA", [_hcode("ON"), _hcode("QC"), _hcode("TOR")])],
    )
    rt = roundtrip_json(StructureMessage(hierarchies=[h_admin, h_stat]))
    assert set(rt.hierarchies.ids()) == {"H_GEO_ADMIN", "H_GEO_STAT"}
    rt_admin = rt.hierarchies["H_GEO_ADMIN"]
    rt_stat = rt.hierarchies["H_GEO_STAT"]
    # The distinct parent graphs are preserved end-to-end.
    assert rt_admin.descendants("ON") == ["TOR"]
    assert rt_stat.descendants("ON") == []


def test_hierarchy_association_json_round_trip() -> None:
    """HierarchyAssociation with all three URN-text refs survives a JSON
    round-trip and resolves linkedHierarchy in the same message (#135)."""
    h = Hierarchy(id="H_GEO", maintainer="TEST", version="1.0", name={"en": "Geo"})
    linked_h_urn = SdmxUrn(package="codelist", artefact_class="Hierarchy", agency="TEST", id="H_GEO", version="1.0")
    dim_urn = SdmxUrn(
        package="datastructure",
        artefact_class="Dimension",
        agency="TEST",
        id="DSD_GEO",
        version="1.0",
        item_id="REF_AREA",
    )
    ctx_urn = SdmxUrn(package="datastructure", artefact_class="Dataflow", agency="TEST", id="DF_GEO", version="1.0")
    ha = HierarchyAssociation(
        id="HA_REF_AREA",
        maintainer="TEST",
        version="1.0",
        name={"en": "REF_AREA → Geo"},
        linked_hierarchy=Ref[Hierarchy].unresolved(linked_h_urn),
        linked_object=Ref.unresolved(dim_urn),
        context_object=Ref.unresolved(ctx_urn),
    )
    rt = roundtrip_json(StructureMessage(hierarchies=[h], hierarchy_associations=[ha]))
    assert rt.hierarchy_associations.ids() == ["HA_REF_AREA"]
    rt_ha = rt.hierarchy_associations["HA_REF_AREA"]
    assert rt_ha.linked_hierarchy is not None
    assert str(rt_ha.linked_hierarchy.urn) == str(linked_h_urn)
    # linkedHierarchy resolves against in-message Hierarchies.
    assert rt_ha.linked_hierarchy.resolved
    assert rt_ha.linked_hierarchy().id == "H_GEO"
    assert rt_ha.linked_object is not None
    assert rt_ha.linked_object.urn.artefact_class == "Dimension"
    assert rt_ha.linked_object.urn.item_id == "REF_AREA"
    assert rt_ha.context_object is not None
    assert rt_ha.context_object.urn.artefact_class == "Dataflow"


def test_hierarchy_association_without_context_json_round_trip() -> None:
    """Unscoped HierarchyAssociation (no contextObject) survives the JSON round-trip."""
    linked_h_urn = SdmxUrn(package="codelist", artefact_class="Hierarchy", agency="TEST", id="H_GEO", version="1.0")
    dim_urn = SdmxUrn(
        package="datastructure",
        artefact_class="Dimension",
        agency="TEST",
        id="DSD_GEO",
        version="1.0",
        item_id="REF_AREA",
    )
    ha = HierarchyAssociation(
        name={"en": "HA_GLOBAL"},
        id="HA_GLOBAL",
        maintainer="TEST",
        version="1.0",
        linked_hierarchy=Ref[Hierarchy].unresolved(linked_h_urn),
        linked_object=Ref.unresolved(dim_urn),
    )
    rt = roundtrip_json(StructureMessage(hierarchy_associations=[ha]))
    rt_ha = rt.hierarchy_associations["HA_GLOBAL"]
    assert rt_ha.context_object is None


def test_hierarchical_code_with_code_ref_json_round_trip() -> None:
    """A HierarchicalCode that references an underlying Code by URN."""
    from sdmxlib.model.codelist import Code

    code_urn: SdmxUrn[Code] = SdmxUrn(
        package="codelist",
        artefact_class="Code",
        agency="SDMX",
        id="CL_GEO",
        version="1.0",
        item_id="FR",
    )
    h = Hierarchy(
        id="H_CODED",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Code-linked"},
        tree=[
            HierarchicalCode(
                id="FR_NODE",
                code=Ref[Code].unresolved(code_urn),
            ),
        ],
    )
    rt = roundtrip_json(StructureMessage(hierarchies=[h]))
    rt_h = rt.hierarchies["H_CODED"]
    node = rt_h.tree["FR_NODE"]
    assert node.code is not None
    assert node.code.urn.item_id == "FR"


# ── CodelistExtension (the last dict island, now typed — #186) ────────────────


def _extending_codelist(selection: CodeSelection | None, *, prefix: str | None = None) -> Codelist:
    """A codelist that composes CL_BASE via one CodelistExtension."""
    base_urn = Codelist.parse_urn("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_BASE(1.0)")
    return Codelist(
        id="CL_EXT",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Extending list"},
        codes=ItemList([Code(id="X", name={"en": "X"})]),
        extensions=(
            CodelistExtension(codelist=Ref[Codelist].unresolved(base_urn), selection=selection, prefix=prefix),
        ),
    )


def _only_ext(msg: StructureMessage) -> CodelistExtension:
    cl = roundtrip_json(msg).codelists["CL_EXT"]
    assert len(cl.extensions) == 1
    return cl.extensions[0]


def test_codelist_extension_inclusive_with_all_member_shapes_round_trips() -> None:
    # Covers the two anyOf/union quirks at once: a bare-string member (no
    # cascade) and object members carrying each CascadeSelection — true /
    # false / excluderoot.
    selection = CodeSelection(
        mode=InclusionMode.INCLUDE,
        members=(
            MemberValue(value="A"),  # bare string on the wire
            MemberValue(value="B", cascade=CascadeSelection.INCLUDE_DESCENDANTS),  # cascadeValues: true
            MemberValue(value="C", cascade=CascadeSelection.SELF_ONLY),  # cascadeValues: false
            MemberValue(value="D", cascade=CascadeSelection.EXCLUDE_ROOT),  # cascadeValues: "excluderoot"
        ),
    )
    ext = _only_ext(StructureMessage(codelists=[_extending_codelist(selection, prefix="PFX_")]))

    assert str(ext.codelist.urn).endswith("Codelist=SDMX:CL_BASE(1.0)")
    assert ext.prefix == "PFX_"
    assert ext.selection is not None
    assert ext.selection.mode is InclusionMode.INCLUDE
    assert [(m.value, m.cascade) for m in ext.selection.members] == [
        ("A", None),
        ("B", CascadeSelection.INCLUDE_DESCENDANTS),
        ("C", CascadeSelection.SELF_ONLY),
        ("D", CascadeSelection.EXCLUDE_ROOT),
    ]


def test_codelist_extension_exclusive_selection_round_trips() -> None:
    # The xs:choice quirk: exclusive mode must land on exclusiveCodeSelection
    # and read back as InclusionMode.EXCLUDE.
    selection = CodeSelection(mode=InclusionMode.EXCLUDE, members=(MemberValue(value="A"),))
    ext = _only_ext(StructureMessage(codelists=[_extending_codelist(selection)]))
    assert ext.selection is not None
    assert ext.selection.mode is InclusionMode.EXCLUDE
    assert ext.prefix is None


def test_codelist_extension_without_selection_round_trips() -> None:
    # Whole-codelist extension: no selection, just the codelist ref (+ prefix).
    ext = _only_ext(StructureMessage(codelists=[_extending_codelist(None, prefix="P_")]))
    assert ext.selection is None
    assert ext.prefix == "P_"


def test_codelist_extension_wire_shape() -> None:
    # Lock the emitted wire shape: bare string vs {value, cascadeValues},
    # boolean vs "excluderoot", and the inclusiveCodeSelection key.
    import msgspec

    selection = CodeSelection(
        mode=InclusionMode.INCLUDE,
        members=(
            MemberValue(value="A"),
            MemberValue(value="B", cascade=CascadeSelection.INCLUDE_DESCENDANTS),
            MemberValue(value="D", cascade=CascadeSelection.EXCLUDE_ROOT),
        ),
    )
    doc = msgspec.json.decode(write_json(StructureMessage(codelists=[_extending_codelist(selection)])))
    ext = doc["data"]["codelists"][0]["codelistExtensions"][0]
    mvs = ext["inclusiveCodeSelection"]["memberValues"]
    assert mvs[0] == "A"  # bare string
    assert mvs[1] == {"value": "B", "cascadeValues": True}  # JSON boolean
    assert mvs[2] == {"value": "D", "cascadeValues": "excluderoot"}  # string literal
