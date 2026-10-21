"""Tests for ``reg.applicable_hierarchies`` and ``reg.unique_hierarchy``."""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import (
    AmbiguousHierarchyError,
    Hierarchy,
    HierarchyAssociation,
)
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _dsd() -> DataStructure:
    return DataStructure(
        name={"en": "CENSUS"},
        id="CENSUS",
        maintainer="STATCAN",
        version="1.0",
        dimensions=[
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
            Dimension(id="INDICATOR", concept=concept_ref("INDICATOR")),
        ],
    )


def _dataflow(structure: DataStructure | None = None, id_: str = "DF_CENSUS") -> Dataflow:
    return Dataflow(
        name={"en": "x"},
        id=id_,
        maintainer="STATCAN",
        version="1.0",
        structure=Ref.to(structure) if structure is not None else None,
    )


def _hierarchy(id_: str) -> Hierarchy:
    return Hierarchy(name={"en": "x"}, id=id_, maintainer="STATCAN", version="1.0")


def _dim_urn(dsd: DataStructure, dim_id: str) -> SdmxUrn[Dimension]:
    dsd_urn = artefact_urn(dsd)
    return SdmxUrn.make(
        Dimension,
        package="datastructure",
        artefact_class="Dimension",
        agency=dsd_urn.agency,
        id=dsd_urn.id,
        version=dsd_urn.version,
        item_id=dim_id,
    )


def _association(
    *,
    id_: str,
    dim_urn: SdmxUrn[Dimension],
    hierarchy: Hierarchy,
    context_dataflow: Dataflow | None = None,
) -> HierarchyAssociation:
    ctx: Ref[Dataflow] | None = Ref.to(context_dataflow) if context_dataflow is not None else None
    return HierarchyAssociation(
        name={"en": "x"},
        id=id_,
        maintainer="STATCAN",
        version="1.0",
        linked_hierarchy=Ref.to(hierarchy),
        linked_object=Ref[Dimension].unresolved(dim_urn),
        context_object=ctx,
    )


# ── No-match cases ──────────────────────────────────────────────────────


def test_dataflow_without_structure_returns_empty():
    reg = InMemoryRegistry()
    df = _dataflow(structure=None)
    reg.add(df)
    assert reg.applicable_hierarchies(df, "REF_AREA") == []
    assert reg.unique_hierarchy(df, "REF_AREA") is None


def test_no_associations_returns_empty():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    reg.add_all([dsd, df])
    assert reg.applicable_hierarchies(df, "REF_AREA") == []
    assert reg.unique_hierarchy(df, "REF_AREA") is None


def test_association_for_other_dim_is_ignored():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    h = _hierarchy("H_GEO")
    other_dim_urn = _dim_urn(dsd, "INDICATOR")
    reg.add_all([dsd, df, h, _association(id_="HA1", dim_urn=other_dim_urn, hierarchy=h)])
    assert reg.applicable_hierarchies(df, "REF_AREA") == []
    assert reg.unique_hierarchy(df, "REF_AREA") is None


def test_association_for_other_dataflow_context_is_ignored():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd, id_="DF_CENSUS")
    other_df = _dataflow(dsd, id_="DF_OTHER")
    h = _hierarchy("H_GEO")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    reg.add_all(
        [
            dsd,
            df,
            other_df,
            h,
            _association(id_="HA1", dim_urn=dim_urn, hierarchy=h, context_dataflow=other_df),
        ]
    )
    assert reg.applicable_hierarchies(df, "REF_AREA") == []
    assert reg.unique_hierarchy(df, "REF_AREA") is None


# ── Single-match cases ──────────────────────────────────────────────────


def test_single_unscoped_association_matches():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    h = _hierarchy("H_GEO")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    reg.add_all([dsd, df, h, _association(id_="HA1", dim_urn=dim_urn, hierarchy=h)])
    result = reg.unique_hierarchy(df, "REF_AREA")
    assert result is not None
    assert str(result.urn) == str(artefact_urn(h))
    applicable = reg.applicable_hierarchies(df, "REF_AREA")
    assert [str(r.urn) for r in applicable] == [str(artefact_urn(h))]


def test_single_dataflow_scoped_association_matches():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    h = _hierarchy("H_GEO")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    reg.add_all([dsd, df, h, _association(id_="HA1", dim_urn=dim_urn, hierarchy=h, context_dataflow=df)])
    result = reg.unique_hierarchy(df, "REF_AREA")
    assert result is not None
    assert str(result.urn) == str(artefact_urn(h))


# ── Precedence ──────────────────────────────────────────────────────────


def test_unique_hierarchy_picks_dataflow_scoped_over_unscoped():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    h_admin = _hierarchy("H_ADMIN")
    h_stat = _hierarchy("H_STAT")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    reg.add_all(
        [
            dsd,
            df,
            h_admin,
            h_stat,
            _association(id_="HA_GLOBAL", dim_urn=dim_urn, hierarchy=h_admin),
            _association(id_="HA_SCOPED", dim_urn=dim_urn, hierarchy=h_stat, context_dataflow=df),
        ]
    )
    result = reg.unique_hierarchy(df, "REF_AREA")
    assert result is not None
    assert str(result.urn) == str(artefact_urn(h_stat))


def test_applicable_hierarchies_returns_scoped_first_then_unscoped():
    """Mixed scoped + unscoped HAs return scoped first (lex by HA URN),
    then unscoped (also lex)."""
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    h_admin = _hierarchy("H_ADMIN")
    h_stat = _hierarchy("H_STAT")
    h_alt = _hierarchy("H_ALT")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    reg.add_all(
        [
            dsd,
            df,
            h_admin,
            h_stat,
            h_alt,
            # Unscoped first by addition order, but should sort lex within scope
            _association(id_="HA_Z_UNSCOPED", dim_urn=dim_urn, hierarchy=h_admin),
            _association(id_="HA_A_UNSCOPED", dim_urn=dim_urn, hierarchy=h_alt),
            _association(id_="HA_M_SCOPED", dim_urn=dim_urn, hierarchy=h_stat, context_dataflow=df),
        ]
    )
    result = reg.applicable_hierarchies(df, "REF_AREA")
    # Scoped HA first (only one), then unscoped lex-sorted by HA URN.
    assert [r.urn.id for r in result] == ["H_STAT", "H_ALT", "H_ADMIN"]


# ── Ambiguity ───────────────────────────────────────────────────────────


def test_two_unscoped_unique_raises_but_applicable_lists():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    h1 = _hierarchy("H_A")
    h2 = _hierarchy("H_B")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    reg.add_all(
        [
            dsd,
            df,
            h1,
            h2,
            _association(id_="HA1", dim_urn=dim_urn, hierarchy=h1),
            _association(id_="HA2", dim_urn=dim_urn, hierarchy=h2),
        ]
    )
    with pytest.raises(AmbiguousHierarchyError) as exc:
        reg.unique_hierarchy(df, "REF_AREA")
    assert len(exc.value.ha_urns) == 2

    applicable = reg.applicable_hierarchies(df, "REF_AREA")
    assert {r.urn.id for r in applicable} == {"H_A", "H_B"}


def test_two_dataflow_scoped_unique_raises_but_applicable_lists():
    reg = InMemoryRegistry()
    dsd = _dsd()
    df = _dataflow(dsd)
    h1 = _hierarchy("H_A")
    h2 = _hierarchy("H_B")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    reg.add_all(
        [
            dsd,
            df,
            h1,
            h2,
            _association(id_="HA1", dim_urn=dim_urn, hierarchy=h1, context_dataflow=df),
            _association(id_="HA2", dim_urn=dim_urn, hierarchy=h2, context_dataflow=df),
        ]
    )
    with pytest.raises(AmbiguousHierarchyError):
        reg.unique_hierarchy(df, "REF_AREA")
    assert len(reg.applicable_hierarchies(df, "REF_AREA")) == 2


# ── DuckDB-backed LocalRegistry (regression for #136) ────────────────────────


def test_persisted_registry_round_trips_hierarchy_association():
    """LocalRegistry.add(HierarchyAssociation) persists, reads back, and
    feeds the registry hierarchy methods correctly."""
    dsd = _dsd()
    df = _dataflow(dsd)
    h = _hierarchy("H_GEO")
    dim_urn = _dim_urn(dsd, "REF_AREA")
    ha = _association(id_="HA1", dim_urn=dim_urn, hierarchy=h, context_dataflow=df)

    with LocalRegistry(":memory:") as reg:
        reg.add(dsd)
        reg.add(df)
        reg.add(h)
        reg.add(ha)  # would raise TypeError pre-fix

        got = reg.get_all(HierarchyAssociation)
        assert len(got) == 1
        assert got[0].id == "HA1"
        assert got[0].linked_hierarchy is not None
        assert got[0].linked_hierarchy.urn.id == "H_GEO"
        assert got[0].linked_object is not None
        assert got[0].linked_object.urn.item_id == "REF_AREA"
        assert got[0].context_object is not None
        assert got[0].context_object.urn.id == "DF_CENSUS"

        result = reg.unique_hierarchy(df, "REF_AREA")
        assert result is not None
        assert str(result.urn) == str(artefact_urn(h))
