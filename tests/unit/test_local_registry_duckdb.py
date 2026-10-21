"""Tests for the DuckDB-backed LocalRegistry."""

import logging
import re

import pytest

from sdmxlib.local import LazyCodelist, LocalRegistry
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist, CodelistLike
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import code_urn, concept_ref, minimal_dsd
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_NAME = InternationalString(en="Test")
_NAME_FR = InternationalString(en="Test", fr="Essai")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_cl(
    cl_id: str = "CL_FREQ",
    agency: str = "SDMX",
    codes: list[Code] | None = None,
) -> Codelist:
    return Codelist(
        id=cl_id,
        maintainer=agency,
        version="1.0",
        name=_NAME_FR,
        codes=codes
        or [
            Code(id="A", name=InternationalString(en="Annual", fr="Annuel")),
            Code(id="Q", name=InternationalString(en="Quarterly", fr="Trimestriel")),
            Code(id="M", name=InternationalString(en="Monthly", fr="Mensuel")),
        ],
    )


def _make_cs(cs_id: str = "CS_TEST", agency: str = "SDMX") -> ConceptScheme:
    return ConceptScheme(
        id=cs_id,
        maintainer=agency,
        version="1.0",
        name=_NAME,
        concepts=[Concept(id="FREQ", name=_NAME)],
    )


def _make_df(df_id: str = "DF_TEST", agency: str = "SDMX") -> Dataflow:
    return Dataflow(id=df_id, maintainer=agency, version="1.0", name=_NAME)


# ── Basic CRUD ───────────────────────────────────────────────────────────────


def test_empty_registry():
    with LocalRegistry(":memory:") as local:
        assert len(local) == 0
        assert "empty" in repr(local)


def test_add_and_get_dataflow():
    with LocalRegistry(":memory:") as local:
        df = _make_df()
        local.add(df)
        urn = Dataflow.urn_for(agency="SDMX", id="DF_TEST", version="1.0")
        result = local.get(urn)
        assert result is not None
        assert result.id == "DF_TEST"


def test_add_and_get_concept_scheme():
    with LocalRegistry(":memory:") as local:
        cs = _make_cs()
        local.add(cs)
        urn = ConceptScheme.urn_for(agency="SDMX", id="CS_TEST", version="1.0")
        result = local.get(urn)
        assert result is not None
        assert result.id == "CS_TEST"
        assert result.concepts["FREQ"].id == "FREQ"


def test_add_and_get_dsd():
    with LocalRegistry(":memory:") as local:
        dsd = minimal_dsd()
        local.add(dsd)
        urn = DataStructure.urn_for(agency="SDMX", id="DSD_TEST", version="1.0")
        result = local.get(urn)
        assert result is not None
        assert result.id == "DSD_TEST"


def test_contains():
    with LocalRegistry(":memory:") as local:
        df = _make_df()
        urn = Dataflow.urn_for(agency="SDMX", id="DF_TEST", version="1.0")
        assert urn not in local
        local.add(df)
        assert urn in local


def test_len():
    with LocalRegistry(":memory:") as local:
        local.add(_make_df())
        local.add(_make_cs())
        assert len(local) == 2


def test_get_all():
    with LocalRegistry(":memory:") as local:
        local.add(_make_df("DF_1"))
        local.add(_make_df("DF_2"))
        local.add(_make_cs())
        dfs = local.get_all(Dataflow)
        assert len(dfs) == 2


def test_get_nonexistent_returns_none():
    with LocalRegistry(":memory:") as local:
        urn = Dataflow.urn_for(agency="SDMX", id="NOPE", version="1.0")
        assert local.get(urn) is None


# ── Mutations ────────────────────────────────────────────────────────────────


def test_drop():
    with LocalRegistry(":memory:") as local:
        df = _make_df()
        local.add(df)
        urn = Dataflow.urn_for(agency="SDMX", id="DF_TEST", version="1.0")
        assert local.drop(urn)
        assert urn not in local
        assert len(local) == 0


def test_drop_nonexistent_returns_false():
    with LocalRegistry(":memory:") as local:
        urn = Dataflow.urn_for(agency="SDMX", id="NOPE", version="1.0")
        assert not local.drop(urn)


def test_replace():
    with LocalRegistry(":memory:") as local:
        df1 = _make_df("DF_OLD")
        local.add(df1)
        old_urn = Dataflow.urn_for(agency="SDMX", id="DF_OLD", version="1.0")
        df2 = _make_df("DF_NEW")
        local.replace(old_urn, df2)
        assert old_urn not in local
        new_urn = Dataflow.urn_for(agency="SDMX", id="DF_NEW", version="1.0")
        assert new_urn in local


def test_add_overwrites_existing():
    with LocalRegistry(":memory:") as local:
        cl1 = _make_cl(codes=[Code(id="A", name=_NAME)])
        local.add(cl1)
        cl2 = _make_cl(codes=[Code(id="A", name=_NAME), Code(id="B", name=_NAME)])
        local.add(cl2)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        result = local.get(urn)
        assert result is not None
        assert len(result) == 2


# ── transaction() ────────────────────────────────────────────────────────────


def test_transaction_commits_on_clean_exit():
    with LocalRegistry(":memory:") as local:
        with local.transaction():
            local.add(_make_df("DF_A"))
            local.add(_make_df("DF_B"))
        assert len(local) == 2


def test_transaction_rolls_back_on_exception():
    with LocalRegistry(":memory:") as local:
        local.add(_make_df("DF_PRE"))

        def _failing_batch() -> None:
            with local.transaction():
                local.add(_make_df("DF_NEW"))
                msg = "boom"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            _failing_batch()
        # The pre-existing artefact survives; the in-flight add is gone.
        assert len(local) == 1
        assert local.get(Dataflow.urn_for(agency="SDMX", id="DF_PRE", version="1.0")) is not None
        assert local.get(Dataflow.urn_for(agency="SDMX", id="DF_NEW", version="1.0")) is None


def test_transaction_nested_is_no_op():
    # Nested ``with reg.transaction()`` blocks join the outer transaction
    # rather than emitting a second BEGIN (DuckDB rejects nesting).
    with LocalRegistry(":memory:") as local:
        with local.transaction():
            local.add(_make_df("DF_A"))
            with local.transaction():
                local.add(_make_df("DF_B"))
            local.add(_make_df("DF_C"))
        assert len(local) == 3


def test_add_all_uses_transaction():
    # add_all is a thin wrapper over transaction() — covered by its own
    # commit semantics. This guards against future refactors that drop
    # the wrap.
    with LocalRegistry(":memory:") as local:
        local.add_all([_make_df("DF_A"), _make_df("DF_B"), _make_df("DF_C")])
        assert len(local) == 3


# ── DEBUG-level instrumentation (#142) ───────────────────────────────────────


def test_no_log_records_when_logger_disabled(caplog):
    # Default WARNING level — no DEBUG records emitted, no measurement cost.
    caplog.set_level(logging.WARNING, logger="sdmxlib.local.registry")
    with LocalRegistry(":memory:") as local:
        local.add_all([_make_df("DF_A"), _make_df("DF_B")])
    assert not any(r.name == "sdmxlib.local.registry" for r in caplog.records)


def test_per_artefact_record_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="sdmxlib.local.registry")
    with LocalRegistry(":memory:") as local:
        local.add_all([_make_df("DF_A"), _make_df("DF_B"), _make_cs()])

    persist_records = [r.getMessage() for r in caplog.records if "artefact_persist" in r.getMessage()]
    assert len(persist_records) == 3
    assert any("class=Dataflow" in m for m in persist_records)
    assert any("class=ConceptScheme" in m for m in persist_records)
    # Each record carries a non-trivial wall measurement.
    for m in persist_records:
        wall_match = re.search(r"wall=(\d+\.\d+)", m)
        assert wall_match is not None
        assert float(wall_match.group(1)) >= 0.0


def test_transaction_summary_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="sdmxlib.local.registry")
    with LocalRegistry(":memory:") as local:
        local.add_all([_make_df("DF_A"), _make_df("DF_B"), _make_cs()])

    summaries = [r.getMessage() for r in caplog.records if "transaction_summary" in r.getMessage()]
    assert len(summaries) == 1
    summary = summaries[0]
    assert "count=3" in summary
    # Class breakdown is sorted alphabetically, so ConceptScheme precedes Dataflow.
    assert "ConceptScheme:1" in summary
    assert "Dataflow:2" in summary


def test_single_add_outside_transaction_logs_per_artefact_but_no_summary(caplog):
    # Plain add() outside transaction() emits the per-artefact line but
    # no summary (the summary is a transaction()-exit hook).
    caplog.set_level(logging.DEBUG, logger="sdmxlib.local.registry")
    with LocalRegistry(":memory:") as local:
        local.add(_make_df("DF_A"))

    msgs = [r.getMessage() for r in caplog.records]
    assert any("artefact_persist" in m and "class=Dataflow" in m for m in msgs)
    assert not any("transaction_summary" in m for m in msgs)


# ── Codelist normalization ───────────────────────────────────────────────────


def test_codelist_returns_lazy_by_default():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        result = local.get(urn)
        assert isinstance(result, LazyCodelist)


def test_codelist_eager_returns_real_codelist():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        result = local.get(urn, eager=True)
        assert isinstance(result, Codelist)
        assert len(result.codes) == 3


def test_lazy_codelist_metadata():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        assert present(lazy).id == "CL_FREQ"
        assert present(lazy).agency_id == "SDMX"
        assert present(lazy).version == "1.0"
        assert present(lazy).name["en"] == "Test"
        assert present(lazy).name["fr"] == "Essai"


def test_lazy_codelist_len():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        assert lazy is not None
        assert len(lazy) == 3


def test_lazy_codelist_getitem_by_id():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        assert lazy is not None
        code = lazy.codes["A"]
        assert code.id == "A"
        assert code.name["en"] == "Annual"


def test_lazy_codelist_getitem_missing_raises():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        with pytest.raises(KeyError):
            present(lazy).codes["NOPE"]


def test_lazy_codelist_iteration():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        ids = [c.id for c in present(lazy).codes.collect()]
        assert ids == ["A", "Q", "M"]


def test_lazy_codelist_contains():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        assert lazy is not None
        assert "A" in lazy
        assert "NOPE" not in lazy


def test_lazy_codelist_ids():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        assert present(lazy).codes.ids() == ["A", "Q", "M"]


def test_lazy_codelist_get():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        assert present(lazy).codes.get("A") is not None
        assert present(lazy).codes.get("NOPE") is None


def test_lazy_codelist_materialize():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        real = present(lazy).materialize()
        assert isinstance(real, Codelist)
        assert len(real.codes) == 3
        assert real.codes["A"].name["en"] == "Annual"


# ── code_map ─────────────────────────────────────────────────────────────────


def test_code_map_on_lazy_codelist():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        cm = present(lazy).code_map(lang="en")
        assert cm == {"A": "Annual", "Q": "Quarterly", "M": "Monthly"}


def test_code_map_french():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        cm = present(lazy).code_map(lang="fr")
        assert cm == {"A": "Annuel", "Q": "Trimestriel", "M": "Mensuel"}


def test_registry_code_map():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        cm = local.code_map(urn, lang="en")
        assert cm == {"A": "Annual", "Q": "Quarterly", "M": "Monthly"}


# ── dsd_code_map ─────────────────────────────────────────────────────────────


def test_dsd_code_map():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        dim = Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].unresolved(cl_urn))
        dsd = DataStructure(
            id="DSD_TEST",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            dimensions=[dim],
        )
        local.add(dsd)
        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_TEST", version="1.0")
        cm = local.dsd_code_map(dsd_urn, lang="en")
        assert "FREQ" in cm
        assert cm["FREQ"]["A"] == "Annual"


# ── Roots and children ───────────────────────────────────────────────────────


def test_lazy_codelist_roots():
    codes = [
        Code(id="A", name=_NAME),
        Code(id="A1", name=_NAME, parent_id="A"),
        Code(id="B", name=_NAME),
    ]
    with LocalRegistry(":memory:") as local:
        cl = _make_cl(codes=codes)
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        roots = present(lazy).roots().collect()
        assert [c.id for c in roots] == ["A", "B"]


def test_lazy_codelist_narrower():
    codes = [
        Code(id="A", name=_NAME),
        Code(id="A1", name=_NAME, parent_id="A"),
        Code(id="A2", name=_NAME, parent_id="A"),
        Code(id="B", name=_NAME),
    ]
    with LocalRegistry(":memory:") as local:
        cl = _make_cl(codes=codes)
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        children = present(lazy).narrower("A").collect()
        assert [c.id for c in children] == ["A1", "A2"]


def test_registry_codelist_accessor():
    codes = [
        Code(id="A", name=_NAME),
        Code(id="A1", name=_NAME, parent_id="A"),
        Code(id="A2", name=_NAME, parent_id="A"),
        Code(id="B", name=_NAME),
    ]
    with LocalRegistry(":memory:") as local:
        cl = _make_cl(codes=codes)
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.codelist(urn)
        assert lazy is not None
        assert lazy.roots().ids() == ["A", "B"]
        assert lazy.narrower("A").ids() == ["A1", "A2"]
        assert lazy.narrower("B").ids() == []


def _add_geo_codelist(local: LocalRegistry) -> SdmxUrn[Codelist]:
    """Persist CA → ON/QC; ON → TOR/OTT; QC → MTL. Returns codelist URN."""
    codes = [
        Code(id="CA", name=_NAME),
        Code(id="ON", name=_NAME, parent_id="CA"),
        Code(id="QC", name=_NAME, parent_id="CA"),
        Code(id="TOR", name=_NAME, parent_id="ON"),
        Code(id="OTT", name=_NAME, parent_id="ON"),
        Code(id="MTL", name=_NAME, parent_id="QC"),
    ]
    cl = _make_cl(codes=codes)
    local.add(cl)
    return Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")


def test_lazy_codelist_depths():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_codelist(local)
        lazy = local.get(urn)
        assert present(lazy).depths() == {"CA": 0, "ON": 1, "QC": 1, "TOR": 2, "OTT": 2, "MTL": 2}


def test_lazy_codelist_broader_map():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_codelist(local)
        lazy = local.get(urn)
        assert present(lazy).broader_map() == {
            "CA": None,
            "ON": "CA",
            "QC": "CA",
            "TOR": "ON",
            "OTT": "ON",
            "MTL": "QC",
        }


def test_lazy_codelist_narrower_map():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_codelist(local)
        lazy = local.get(urn)
        nm = present(lazy).narrower_map()
        assert nm["CA"] == ("ON", "QC")
        assert nm["ON"] == ("TOR", "OTT")
        assert nm["QC"] == ("MTL",)
        assert nm["TOR"] == ()


def test_lazy_codelist_ancestors_map():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_codelist(local)
        lazy = local.get(urn)
        am = present(lazy).ancestors_map()
        assert am["CA"] == ()
        assert am["ON"] == ("CA",)
        assert am["TOR"] == ("CA", "ON")
        assert am["MTL"] == ("CA", "QC")


def test_lazy_codelist_descendants_map():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_codelist(local)
        lazy = local.get(urn)
        dm = present(lazy).descendants_map()
        assert set(dm["CA"]) == {"ON", "QC", "TOR", "OTT", "MTL"}
        assert dm["ON"] == ("TOR", "OTT") or dm["ON"] == ("OTT", "TOR")
        assert dm["TOR"] == ()


def test_lazy_codelist_inherit_along_broader():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_codelist(local)
        lazy = local.get(urn)
        out = present(lazy).inherit_along_broader({"CA": "country", "ON": "province"})
        assert out == {
            "CA": "country",
            "ON": "province",
            "QC": "country",
            "TOR": "province",
            "OTT": "province",
            "MTL": "country",
        }


def test_narrower_transitive_walks_descendants():
    """narrower_transitive returns full descendant set via ancestor_ids array."""
    codes = [
        Code(id="CA", name=_NAME),
        Code(id="ON", name=_NAME, parent_id="CA"),
        Code(id="QC", name=_NAME, parent_id="CA"),
        Code(id="TOR", name=_NAME, parent_id="ON"),
        Code(id="OTT", name=_NAME, parent_id="ON"),
        Code(id="MTL", name=_NAME, parent_id="QC"),
    ]
    with LocalRegistry(":memory:") as local:
        cl = _make_cl(codes=codes)
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.codelist(urn)
        assert lazy is not None
        # all descendants of CA
        assert set(lazy.narrower_transitive("CA").ids()) == {"ON", "QC", "TOR", "OTT", "MTL"}
        # one hop only — equivalent to narrower
        assert set(lazy.narrower_transitive("CA", depth=1).ids()) == {"ON", "QC"}


def test_registry_codelist_accessor_missing():
    with LocalRegistry(":memory:") as local:
        urn = Codelist.urn_for(agency="SDMX", id="CL_MISSING", version="1.0")
        assert local.codelist(urn) is None


# ── Hierarchy ────────────────────────────────────────────────────────────────


def test_hierarchy_round_trip():
    h = Hierarchy(
        id="H_TEST",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        tree=[
            HierarchicalCode(
                id="ROOT",
                code=code_urn("CL", "ROOT"),
                children=[
                    HierarchicalCode(id="CHILD1", code=code_urn("CL", "CHILD1")),
                    HierarchicalCode(id="CHILD2", code=code_urn("CL", "CHILD2")),
                ],
            ),
        ],
    )
    with LocalRegistry(":memory:") as local:
        local.add(h)
        urn = Hierarchy.urn_for(agency="SDMX", id="H_TEST", version="1.0")
        result = local.get(urn)
        assert result is not None
        # LazyHierarchy: SKOS-style access via roots() / narrower() / nodes
        assert result.roots().ids() == ["ROOT"]
        assert set(result.narrower("ROOT").ids()) == {"CHILD1", "CHILD2"}
        assert "ROOT.CHILD1" in result
        assert "CHILD1" not in result, "a nested node's bare id is not a path (#439)"


def _add_geo_hierarchy(local: LocalRegistry) -> SdmxUrn[Hierarchy]:
    """Persist a 5-node geographic hierarchy. Returns hierarchy URN."""
    h = Hierarchy(
        id="H_GEO",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        tree=[
            HierarchicalCode(
                id="CA",
                code=code_urn("CL", "CA"),
                children=[
                    HierarchicalCode(
                        id="ON",
                        code=code_urn("CL", "ON"),
                        children=[
                            HierarchicalCode(id="TOR", code=code_urn("CL", "TOR")),
                            HierarchicalCode(id="OTT", code=code_urn("CL", "OTT")),
                        ],
                    ),
                    HierarchicalCode(
                        id="QC",
                        code=code_urn("CL", "QC"),
                        children=[HierarchicalCode(id="MTL", code=code_urn("CL", "MTL"))],
                    ),
                ],
            ),
        ],
    )
    local.add(h)
    return Hierarchy.urn_for(agency="SDMX", id="H_GEO", version="1.0")


def test_lazy_hierarchy_skos_verbs():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_hierarchy(local)
        h = local.get(urn)
        assert present(h).roots().ids() == ["CA"]
        assert set(present(h).leaves().ids()) == {"TOR", "OTT", "MTL"}
        assert set(present(h).narrower("CA").ids()) == {"ON", "QC"}
        parent = present(h).broader("CA.ON")
        assert parent is not None
        assert parent.id == "CA"
        assert present(h).broader("CA") is None
        assert set(present(h).narrower_transitive("CA").ids()) == {"ON", "QC", "TOR", "OTT", "MTL"}
        assert set(present(h).narrower_transitive("CA", depth=1).ids()) == {"ON", "QC"}
        assert set(present(h).broader_transitive("CA.ON.TOR").ids()) == {"CA", "ON"}
        assert present(h).depth_of("CA.ON.TOR") == 2


def test_lazy_hierarchy_bulk_maps():
    with LocalRegistry(":memory:") as local:
        urn = _add_geo_hierarchy(local)
        h = local.get(urn)
        lazy = present(h)
        eager = lazy.materialize()
        assert lazy.depths() == {"CA": 0, "CA.ON": 1, "CA.QC": 1, "CA.ON.TOR": 2, "CA.ON.OTT": 2, "CA.QC.MTL": 2}
        assert lazy.broader_map() == {
            "CA": None,
            "CA.ON": "CA",
            "CA.QC": "CA",
            "CA.ON.TOR": "CA.ON",
            "CA.ON.OTT": "CA.ON",
            "CA.QC.MTL": "CA.QC",
        }
        am = lazy.ancestors_map()
        assert am["CA.ON.TOR"] == ("CA", "CA.ON")
        assert am["CA"] == ()
        values = {"CA": "country", "CA.ON": "province"}
        assert lazy.inherit_along_broader(values) == {
            "CA": "country",
            "CA.ON": "province",
            "CA.QC": "country",
            "CA.ON.TOR": "province",
            "CA.ON.OTT": "province",
            "CA.QC.MTL": "country",
        }
        # The lazy maps are the eager ones, in declaration order too.
        assert lazy.depths() == eager.depths()
        assert lazy.broader_map() == eager.broader_map()
        assert lazy.narrower_map() == eager.narrower_map()
        assert list(lazy.narrower_map().items()) == list(eager.narrower_map().items())
        assert lazy.ancestors_map() == eager.ancestors_map()
        assert lazy.descendants_map() == eager.descendants_map()
        assert lazy.inherit_along_broader(values) == eager.inherit_along_broader(values)


def test_lazy_hierarchy_query_filter_by_depth():
    """The lazy node relation composes with predicate filters on depth."""
    from sdmxlib.query.accessors import q

    with LocalRegistry(":memory:") as local:
        urn = _add_geo_hierarchy(local)
        h = local.get(urn)
        shallow = present(h).nodes.filter(q.hcode.depth == 1).ids()
        assert set(shallow) == {"ON", "QC"}
        # Compose narrower() with a filter
        deep_under_ca = present(h).narrower_transitive("CA").filter(q.hcode.depth == 2).ids()
        assert set(deep_under_ca) == {"TOR", "OTT", "MTL"}


# ── Annotations on codes ────────────────────────────────────────────────────


def test_code_annotations_round_trip():
    ann = Annotations([Annotation(type="SOURCE", text=InternationalString(en="Test source"))])
    codes = [Code(id="A", name=_NAME, annotations=ann)]
    with LocalRegistry(":memory:") as local:
        cl = _make_cl(codes=codes)
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        # `with_annotations` went with `CodeQuery` (#306): annotations are no
        # longer opt-in, because the resolver hydrates whole `Code` objects and
        # a half-populated artefact was the surprise the opt-in bought.
        code = present(lazy).codes["A"]
        assert len(code.annotations) == 1
        assert code.annotations[0].type == "SOURCE"
        assert code.annotations[0].text["en"] == "Test source"


# ── Drop cascades codes ─────────────────────────────────────────────────────


def test_drop_codelist_cascades_codes():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        local.drop(urn)
        # Codes should be gone too — verify via get returning None
        assert local.get(urn) is None
        assert len(local) == 0


# ── Ref ──────────────────────────────────────────────────────────────────────


def test_ref_returns_unresolved():
    with LocalRegistry(":memory:") as local:
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        r = local.ref(urn)
        assert not r.resolved
        assert r.urn == urn


# ── Ingest from LocalRegistry ────────────────────────────────────────────────────


def test_ingest_from_registry():
    reg = InMemoryRegistry()
    cl = _make_cl()
    df = _make_df()
    reg.add(cl)
    reg.add(df)

    with LocalRegistry(":memory:") as local:
        local.ingest(reg)
        assert len(local) == 2
        cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        assert cl_urn in local
        df_urn = Dataflow.urn_for(agency="SDMX", id="DF_TEST", version="1.0")
        assert df_urn in local


# ── to_registry escape hatch ────────────────────────────────────────────────


def test_to_registry():
    with LocalRegistry(":memory:") as local:
        cl = _make_cl()
        df = _make_df()
        local.add(cl)
        local.add(df)
        reg = local.to_registry()
        assert len(reg) == 2
        cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        loaded_cl = reg.get(cl_urn)
        assert loaded_cl is not None
        assert loaded_cl.codes["A"].name["en"] == "Annual"


# ── File persistence ────────────────────────────────────────────────────────


def test_persistence(tmp_path):
    db_path = tmp_path / "test.db"
    cl = _make_cl()
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")

    with LocalRegistry(db_path) as local:
        local.add(cl)

    with LocalRegistry(db_path) as local:
        assert urn in local
        lazy = local.get(urn)
        assert present(lazy).codes["A"].name["en"] == "Annual"


def test_re_add_across_reopen_does_not_corrupt_localized_text(tmp_path):
    """Re-adding a previously-persisted artefact across a close/reopen cycle
    must not trip the DuckDB ART-index/heap desync surfaced by #70."""
    db_path = tmp_path / "test.db"
    cl = _make_cl()

    with LocalRegistry(db_path) as local:
        local.add(cl)
    with LocalRegistry(db_path) as local:
        local.add(cl)
    with LocalRegistry(db_path) as local:
        local.add(cl)
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        lazy = local.get(urn)
        assert present(lazy).codes["A"].name["en"] == "Annual"


# ── get_all returns LazyCodelist for codelists ───────────────────────────────


def test_get_all_codelists_returns_lazy():
    with LocalRegistry(":memory:") as local:
        local.add(_make_cl("CL_1"))
        local.add(_make_cl("CL_2"))
        codelists = local.get_all(Codelist)
        assert len(codelists) == 2
        assert all(isinstance(cl, LazyCodelist) for cl in codelists)


# ── Repr ─────────────────────────────────────────────────────────────────────


def test_repr():
    with LocalRegistry(":memory:") as local:
        local.add(_make_cl())
        local.add(_make_df())
        r = repr(local)
        assert "Codelist=1" in r
        assert "Dataflow=1" in r


# ── Ref resolution on get() ────────────────────────────────────────────────


def _make_dsd_with_dims() -> tuple[DataStructure, Codelist, Codelist, ConceptScheme]:
    """Build a DSD with two enumerated dimensions referencing codelists."""
    cl_freq = _make_cl("CL_FREQ", "SDMX")
    cl_area = _make_cl(
        "CL_AREA",
        "SDMX",
        codes=[
            Code(id="CA", name=InternationalString(en="Canada")),
            Code(id="US", name=InternationalString(en="United States")),
        ],
    )
    cs = ConceptScheme(
        id="CS_TEST",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        concepts=[
            Concept(id="FREQ", name=InternationalString(en="Frequency")),
            Concept(id="REF_AREA", name=InternationalString(en="Reference Area")),
        ],
    )

    freq_cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    area_cl_urn = Codelist.urn_for(agency="SDMX", id="CL_AREA", version="1.0")
    freq_concept_urn = Concept.urn_for(agency="SDMX", scheme="CS_TEST", id="FREQ")
    area_concept_urn = Concept.urn_for(agency="SDMX", scheme="CS_TEST", id="REF_AREA")

    freq_rep = Ref[Codelist].unresolved(freq_cl_urn)
    area_rep = Ref[Codelist].unresolved(area_cl_urn)
    freq_concept = Ref[Concept].unresolved(freq_concept_urn)
    area_concept = Ref[Concept].unresolved(area_concept_urn)

    dsd = DataStructure(
        id="DSD_FULL",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        dimensions=[
            Dimension(id="FREQ", representation=freq_rep, concept=freq_concept),
            Dimension(id="REF_AREA", representation=area_rep, concept=area_concept),
        ],
    )
    return dsd, cl_freq, cl_area, cs


def test_get_resolves_codelist_refs():
    """DSD get() resolves codelist Refs to LazyCodelists."""
    dsd, cl_freq, cl_area, cs = _make_dsd_with_dims()
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        local.add(cl_freq)
        local.add(cl_area)
        local.add(cs)

        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_FULL", version="1.0")
        result = local.get(dsd_urn)

        # Codelist Refs should be resolved
        freq_dim = present(result).dimensions["FREQ"]
        assert freq_dim.is_enumerated
        assert isinstance(freq_dim.representation, Ref)
        assert freq_dim.representation.resolved

        # Unwrap gives LazyCodelist
        cl = freq_dim.codelist
        assert isinstance(cl, LazyCodelist)
        assert cl.id == "CL_FREQ"
        assert len(cl) == 3

        area_dim = present(result).dimensions["REF_AREA"]
        assert isinstance(area_dim.representation, Ref)
        assert area_dim.representation.resolved
        area_cl = area_dim.codelist
        assert isinstance(area_cl, LazyCodelist)
        assert area_cl.codes["CA"].name["en"] == "Canada"


def test_get_resolves_concept_refs():
    """DSD get() resolves concept Refs from ConceptScheme."""
    dsd, cl_freq, cl_area, cs = _make_dsd_with_dims()
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        local.add(cl_freq)
        local.add(cl_area)
        local.add(cs)

        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_FULL", version="1.0")
        result = local.get(dsd_urn)

        freq_dim = present(result).dimensions["FREQ"]
        assert present(freq_dim.concept).resolved
        assert freq_dim.concept_identity.name["en"] == "Frequency"


def test_dsd_traversal_code_map():
    """DSD.code_map() works via resolved LazyCodelist Refs."""
    dsd, cl_freq, cl_area, cs = _make_dsd_with_dims()
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        local.add(cl_freq)
        local.add(cl_area)
        local.add(cs)

        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_FULL", version="1.0")
        result = local.get(dsd_urn)
        cm = present(result).code_map(lang="en")

        assert "FREQ" in cm
        assert cm["FREQ"]["A"] == "Annual"
        assert "REF_AREA" in cm
        assert cm["REF_AREA"]["CA"] == "Canada"


def test_dsd_traversal_all_codelists():
    """DSD.all_codelists works with resolved LazyCodelist Refs."""
    dsd, cl_freq, cl_area, cs = _make_dsd_with_dims()
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        local.add(cl_freq)
        local.add(cl_area)
        local.add(cs)

        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_FULL", version="1.0")
        result = local.get(dsd_urn)
        all_cls = present(result).all_codelists

        assert "FREQ" in all_cls
        assert "REF_AREA" in all_cls
        assert isinstance(all_cls["FREQ"], LazyCodelist)


def test_dsd_traversal_column_labels():
    """DSD.column_labels() works with resolved concept Refs."""
    dsd, cl_freq, cl_area, cs = _make_dsd_with_dims()
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        local.add(cl_freq)
        local.add(cl_area)
        local.add(cs)

        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_FULL", version="1.0")
        result = local.get(dsd_urn)
        labels = present(result).column_labels(lang="en")

        assert labels["FREQ"] == "Frequency"
        assert labels["REF_AREA"] == "Reference Area"


def test_missing_ref_stays_unresolved():
    """Refs to artefacts not in the store remain unresolved."""
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_MISSING", version="1.0")
    dsd = DataStructure(
        id="DSD_PARTIAL",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].unresolved(cl_urn)),
        ],
    )
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_PARTIAL", version="1.0")
        result = local.get(dsd_urn)

        freq_dim = present(result).dimensions["FREQ"]
        assert freq_dim.is_enumerated
        assert isinstance(freq_dim.representation, Ref)
        assert not freq_dim.representation.resolved


def test_dimension_codes_via_lazy_codelist():
    """`Dimension.codes` keeps its declared type when the DSD is registry-backed.

    This test used to pin the opposite. A DSD read from a `LocalRegistry`
    resolves its codelist ref to a `LazyCodelist`, whose `codes` is a `Query`,
    so `Dimension.codes` declared `ItemList[Code]` and returned a query — which
    is why the old body needed a `cast` and said the divergence "needs the same
    decision rather than a local patch". #398 made it: asking for `Code`
    objects builds them, and `code_ids()` is the cheap path for callers that
    only wanted ids.
    """
    dsd, cl_freq, cl_area, cs = _make_dsd_with_dims()
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        local.add(cl_freq)
        local.add(cl_area)
        local.add(cs)

        dsd_urn = DataStructure.urn_for(agency="SDMX", id="DSD_FULL", version="1.0")
        result = local.get(dsd_urn)
        assert result is not None
        freq = result.dimensions["FREQ"]

        codes = freq.codes
        assert isinstance(codes, ItemList), f"declared ItemList[Code], got {type(codes).__name__}"
        assert len(codes) == 3, "and len() works on it, which is what used to raise"
        assert codes["A"].name["en"] == "Annual"

        assert freq.code_ids() == codes.ids(), "the cheap path must agree with the built one"

        cl = freq.resolved_codelist
        assert cl is not None
        assert isinstance(cl, LazyCodelist), "still lazy — the fix is not to stop being lazy"
        assert isinstance(cl, CodelistLike)
        assert len(cl) == 3
        assert cl.code_map(lang="en")["A"] == "Annual"
