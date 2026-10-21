"""LocalRegistry reference-metadata queries — ``metadata_for_target`` /
``metadata_report`` (Phase A of the rich-metadata retrieval layer).

The reverse-lookup primitive both the REST view and the search indexer
navigate: given any artefact URN, find the MetadataSets targeting it; and
project a set into flat, MSD-typed sections for chunking.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.registry import artefact_urn
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _code_urn(item_id: str, codelist: str = "CL_TOPIC") -> sl.SdmxUrn[sl.Code]:
    return sl.SdmxUrn.make(
        sl.Code,
        package="codelist",
        artefact_class="Code",
        agency="STC_CENSUS",
        id=codelist,
        version="1.0",
        item_id=item_id,
    )


def _msd() -> sl.MetadataStructure:
    return sl.MetadataStructure(
        id="MSD_RG",
        maintainer="STC_CENSUS",
        version="1.0",
        name={"en": "RG"},
        attributes=[
            sl.MetadataAttribute(
                id="DATA_QUALITY_TOPIC",
                concept=concept_ref("DATA_QUALITY_TOPIC"),
                representation=sl.Facet(type=sl.DataType.XHTML),
            ),
            sl.MetadataAttribute(
                id="CATALOGUE_ID", concept=concept_ref("CATALOGUE_ID"), representation=sl.Facet(type=sl.DataType.String)
            ),
            sl.MetadataAttribute(
                id="NOTE",
                concept=concept_ref("NOTE"),
                representation=sl.Facet(type=sl.DataType.String),
                max_occurs=None,
            ),
        ],
    )


def _mdf(msd: sl.MetadataStructure) -> sl.Metadataflow:
    return sl.Metadataflow(id="MDF_RG", maintainer="STC_CENSUS", version="1.0", name={"en": "RG"}, structure=msd)


def _set(
    structure: sl.Metadataflow | sl.MetadataProvisionAgreement,
    set_id: str,
    targets: list[sl.SdmxUrn[sl.Code]],
) -> sl.MetadataSet:
    return sl.MetadataSet(
        id=set_id,
        maintainer="STC_CENSUS",
        version="1.0",
        structure=structure,
        name={"en": set_id},
        targets=targets,
        attributes=[
            sl.ReportedAttribute(id="DATA_QUALITY_TOPIC", value={"en": "National non-response 1.4%."}),
            sl.ReportedAttribute(id="CATALOGUE_ID", value="98-500-X2021004"),
            sl.ReportedAttribute(id="NOTE", value="n1"),
            sl.ReportedAttribute(id="NOTE", value="n2"),
        ],
    )


# ── metadata_for_target ────────────────────────────────────────────────


def test_metadata_for_target_returns_attached_set() -> None:
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    ms = _set(mdf, "MS_REFGUIDE_004", [code])
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, ms])
        sets = reg.metadata_for_target(code)
        assert [s.id for s in sets] == ["MS_REFGUIDE_004"]
        # Targets carry the code URN without needing ref resolution.
        assert any(t.urn.item_id == "111" for t in sets[0].targets)


def test_metadata_for_target_accepts_str_and_urn() -> None:
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    ms = _set(mdf, "MS_A", [code])
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, ms])
        assert reg.metadata_for_target(code)[0].id == "MS_A"
        assert reg.metadata_for_target(str(code))[0].id == "MS_A"


def test_metadata_for_target_no_match_is_empty() -> None:
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, _set(mdf, "MS_A", [code])])
        assert reg.metadata_for_target(_code_urn("999")) == []


def test_metadata_for_target_multiple_sets_ordered() -> None:
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    b = _set(mdf, "MS_B", [code])
    a = _set(mdf, "MS_A", [code])
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, a, b])
        # Both target the same code; returned ordered by set URN.
        assert [s.id for s in reg.metadata_for_target(code)] == ["MS_A", "MS_B"]


def test_metadata_for_target_structure_filter() -> None:
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    ms = _set(mdf, "MS_A", [code])
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, ms])
        assert [s.id for s in reg.metadata_for_target(code, structure_urn=artefact_urn(mdf))] == ["MS_A"]
        miss = "urn:sdmx:org.sdmx.infomodel.metadatastructure.Metadataflow=STC_CENSUS:OTHER(1.0)"
        assert reg.metadata_for_target(code, structure_urn=miss) == []


# ── metadata_report ────────────────────────────────────────────────────


def test_metadata_report_sections_are_typed() -> None:
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    ms = _set(mdf, "MS_A", [code])
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, ms])
        by_id = {s.path: s for s in reg.metadata_report(artefact_urn(ms))}
        assert by_id["DATA_QUALITY_TOPIC"].text_type == "XHTML"
        assert isinstance(by_id["DATA_QUALITY_TOPIC"].value, InternationalString)
        assert by_id["CATALOGUE_ID"].text_type == "String"
        assert by_id["CATALOGUE_ID"].value == "98-500-X2021004"


def test_metadata_report_preserves_repeated_siblings() -> None:
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    ms = _set(mdf, "MS_A", [code])
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, ms])
        notes = [s for s in reg.metadata_report(artefact_urn(ms)) if s.attribute_id == "NOTE"]
        assert [s.path for s in notes] == ["NOTE", "NOTE[1]"]
        assert [s.value for s in notes] == ["n1", "n2"]


def test_metadata_report_resolves_msd_behind_mpa() -> None:
    # A set whose structure is a MetadataProvisionAgreement — the report must
    # walk MPA → Metadataflow → MSD to type the attributes.
    msd, code = _msd(), _code_urn("111")
    mdf = _mdf(msd)
    mpa = sl.MetadataProvisionAgreement(
        name={"en": "MPA_RG"}, id="MPA_RG", maintainer="STC_CENSUS", version="1.0", metadataflow=mdf
    )
    ms = _set(mpa, "MS_VIA_MPA", [code])
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, mpa, ms])
        types = {s.path: s.text_type for s in reg.metadata_report(artefact_urn(ms))}
        assert types["DATA_QUALITY_TOPIC"] == "XHTML"


def test_metadata_report_missing_set_is_empty() -> None:
    with LocalRegistry(":memory:") as reg:
        reg.add_all([_msd()])
        missing = "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=STC_CENSUS:NOPE(1.0)"
        assert reg.metadata_report(missing) == []


def test_metadata_report_nested_path_leaf_id() -> None:
    # Hierarchical (presentational container) attribute → dotted paths.
    msd = sl.MetadataStructure(
        id="MSD_NEST",
        maintainer="STC_CENSUS",
        version="1.0",
        name={"en": "N"},
        attributes=[
            sl.MetadataAttribute(
                id="QUALITY",
                concept=concept_ref("QUALITY"),
                is_presentational=True,
                children=[
                    sl.MetadataAttribute(
                        id="COVERAGE", concept=concept_ref("COVERAGE"), representation=sl.Facet(type=sl.DataType.String)
                    )
                ],
            )
        ],
    )
    code = _code_urn("111")
    mdf = sl.Metadataflow(id="MDF_NEST", maintainer="STC_CENSUS", version="1.0", name={"en": "N"}, structure=msd)
    ms = sl.MetadataSet(
        id="MS_N",
        maintainer="STC_CENSUS",
        version="1.0",
        structure=mdf,
        name={"en": "N"},
        targets=[code],
        attributes=[sl.ReportedAttribute(id="QUALITY", children=[sl.ReportedAttribute(id="COVERAGE", value="full")])],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, mdf, ms])
        sections = {s.path: s for s in reg.metadata_report(artefact_urn(ms))}
        assert "QUALITY.COVERAGE" in sections
        assert sections["QUALITY.COVERAGE"].attribute_id == "COVERAGE"
        assert sections["QUALITY.COVERAGE"].text_type == "String"
