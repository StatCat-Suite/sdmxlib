"""Tests for sdmxlib.builders.metadata_annotation.

Covers the pure projection (`annotate_structure_message`) with no lake, the
lake-backed derivation (`build_dsd_metadata_map`) — populated and declared
(wildcard) targets, the MPA two-hop, and the bounded-round-trip guard —
storage round-trip of declared targets, and an end-to-end round trip through
all three structure writers (XSD-validated for the SDMX-ML formats).
"""

import json
from pathlib import Path

import pytest
from lxml import etree

from sdmxlib.builders.metadata_annotation import (
    METADATA_ANNOTATION_TYPE,
    NUM_QUERIES,
    DsdMetadataLink,
    annotate_structure_message,
    build_dsd_metadata_map,
    build_metadata_target_links,
)
from sdmxlib.formats.sdmx_json import reader as rjson
from sdmxlib.formats.sdmx_json import writer as wjson
from sdmxlib.formats.sdmx_ml21 import reader as r21
from sdmxlib.formats.sdmx_ml21 import writer as w21
from sdmxlib.formats.sdmx_ml30 import reader as r30
from sdmxlib.formats.sdmx_ml30 import writer as w30
from sdmxlib.local import LocalRegistry
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import artefact_urn
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.writers import put_artefact
from tests.support.factories import memory_cursor
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _msd_urn(id_: str) -> str:
    return str(MetadataStructure.urn_for(agency="TEST", id=id_, version="1.0"))


def _dsd(id_: str = "DSD_X") -> DataStructure:
    return DataStructure(name={"en": "x"}, id=id_, maintainer="TEST")


# ── Pure projection (no lake) ────────────────────────────────────────────


def test_appends_metadata_annotation_with_msd_urn_title() -> None:
    dsd = _dsd()
    msd = _msd_urn("MSD_A")
    msg = StructureMessage(data_structures=[dsd])

    out = annotate_structure_message(msg, {str(artefact_urn(dsd)): [DsdMetadataLink(msd_urn=msd)]})

    ann = out.data_structures[0].annotations.first(METADATA_ANNOTATION_TYPE)
    assert ann is not None
    assert ann.title == msd


def test_projection_preserves_link_order() -> None:
    # The projection emits annotations in the order the map gives them; the
    # DATAFLOW_INFO-first rule is applied upstream by build_dsd_metadata_map
    # (see test_ordering_and_bounded_round_trips_from_lake).
    dsd = _dsd()
    first = _msd_urn("MSD_REF_DATAFLOW_INFO")
    second = _msd_urn("MSD_PLAIN")
    msg = StructureMessage(data_structures=[dsd])

    out = annotate_structure_message(
        msg,
        {str(artefact_urn(dsd)): [DsdMetadataLink(msd_urn=first), DsdMetadataLink(msd_urn=second)]},
    )

    titles = [a.title for a in out.data_structures[0].annotations.by_type(METADATA_ANNOTATION_TYPE)]
    assert titles == [first, second]


def test_idempotent_does_not_duplicate_existing_metadata_annotation() -> None:
    msd = _msd_urn("MSD_A")
    dsd = DataStructure(
        name={"en": "DSD_X"},
        id="DSD_X",
        maintainer="TEST",
        annotations=Annotations([Annotation(type=METADATA_ANNOTATION_TYPE, title=msd)]),
    )
    msg = StructureMessage(data_structures=[dsd])

    out = annotate_structure_message(msg, {str(artefact_urn(dsd)): [DsdMetadataLink(msd_urn=msd)]})

    assert out is msg  # nothing to add → same object
    assert len(out.data_structures[0].annotations.by_type(METADATA_ANNOTATION_TYPE)) == 1


def test_preserves_existing_unrelated_annotations() -> None:
    dsd = DataStructure(
        name={"en": "DSD_X"},
        id="DSD_X",
        maintainer="TEST",
        annotations=Annotations([Annotation(type="OTHER", title="keep")]),
    )
    msg = StructureMessage(data_structures=[dsd])

    out = annotate_structure_message(msg, {str(artefact_urn(dsd)): [DsdMetadataLink(msd_urn=_msd_urn("MSD_A"))]})

    types = out.data_structures[0].annotations.types
    assert "OTHER" in types
    assert METADATA_ANNOTATION_TYPE in types


def test_empty_map_returns_same_message() -> None:
    msg = StructureMessage(data_structures=[_dsd()])
    assert annotate_structure_message(msg, {}) is msg


def test_dsd_absent_from_map_is_untouched_and_original_not_mutated() -> None:
    dsd = _dsd()
    other = _dsd("DSD_OTHER")
    msg = StructureMessage(data_structures=[dsd, other])

    out = annotate_structure_message(msg, {str(artefact_urn(dsd)): [DsdMetadataLink(msd_urn=_msd_urn("MSD_A"))]})

    # The unmapped DSD object is reused as-is.
    assert out.data_structures[1] is other
    # The original DSD object was never mutated (projection returns a copy).
    assert not dsd.annotations
    assert out.data_structures[0] is not dsd


# ── Lake-backed derivation ───────────────────────────────────────────────


class _CountingConn:
    """Delegating DuckDB connection proxy that counts ``execute`` calls."""

    def __init__(self, conn: object) -> None:
        self._conn = conn
        self.execute_calls = 0

    def execute(self, *args: object, **kwargs: object) -> object:
        self.execute_calls += 1
        return self._conn.execute(*args, **kwargs)  # type: ignore[attr-defined]


def _seed_flow(local: LocalRegistry, *, dsd_id: str, flow_id: str) -> tuple[DataStructure, Dataflow]:
    dsd = DataStructure(name={"en": "x"}, id=dsd_id, maintainer="TEST")
    flow = Dataflow(name={"en": "x"}, id=flow_id, maintainer="TEST", structure=dsd)
    local.add(dsd)
    local.add(flow)
    return dsd, flow


def _catalog(local: LocalRegistry) -> LocalRegistry:
    return local


def test_derives_via_metadataflow_backed_set() -> None:
    with LocalRegistry(":memory:") as local:
        dsd, flow = _seed_flow(local, dsd_id="DSD_A", flow_id="DF_A")
        msd = MetadataStructure(name={"en": "MSD_A"}, id="MSD_A", maintainer="TEST")
        mdf = Metadataflow(name={"en": "MDF_A"}, id="MDF_A", maintainer="TEST", structure=msd)
        local.add(msd)
        local.add(mdf)
        local.add(
            MetadataSet(
                name={"en": "MDS_A"}, id="MDS_A", maintainer="TEST", structure=mdf, targets=[artefact_urn(flow)]
            )
        )

        md_map = build_dsd_metadata_map(_catalog(local))

        links = md_map[str(artefact_urn(dsd))]
        assert [link.msd_urn for link in links] == [str(artefact_urn(msd))]
        assert links[0].metadataflow_urn == str(artefact_urn(mdf))
        assert links[0].provision_urn is None


def test_derives_via_metadata_provision_agreement_two_hop() -> None:
    with LocalRegistry(":memory:") as local:
        dsd, flow = _seed_flow(local, dsd_id="DSD_B", flow_id="DF_B")
        msd = MetadataStructure(name={"en": "MSD_B"}, id="MSD_B", maintainer="TEST")
        mdf = Metadataflow(name={"en": "MDF_B"}, id="MDF_B", maintainer="TEST", structure=msd)
        mpa = MetadataProvisionAgreement(name={"en": "MPA_B"}, id="MPA_B", maintainer="TEST", metadataflow=mdf)
        for art in (msd, mdf, mpa):
            local.add(art)
        local.add(
            MetadataSet(
                name={"en": "MDS_B"}, id="MDS_B", maintainer="TEST", structure=mpa, targets=[artefact_urn(flow)]
            )
        )

        md_map = build_dsd_metadata_map(_catalog(local))

        links = md_map[str(artefact_urn(dsd))]
        assert [link.msd_urn for link in links] == [str(artefact_urn(msd))]
        assert links[0].metadataflow_urn == str(artefact_urn(mdf))
        assert links[0].provision_urn == str(artefact_urn(mpa))


def test_non_dataflow_target_does_not_annotate_any_dsd() -> None:
    with LocalRegistry(":memory:") as local:
        _seed_flow(local, dsd_id="DSD_C", flow_id="DF_C")
        msd = MetadataStructure(name={"en": "MSD_C"}, id="MSD_C", maintainer="TEST")
        mdf = Metadataflow(name={"en": "MDF_C"}, id="MDF_C", maintainer="TEST", structure=msd)
        local.add(msd)
        local.add(mdf)
        # Target a codelist, not a dataflow.
        cl_urn = MetadataStructure.urn_for(agency="TEST", id="NOT_A_FLOW", version="1.0")
        local.add(MetadataSet(name={"en": "MDS_C"}, id="MDS_C", maintainer="TEST", structure=mdf, targets=[cl_urn]))

        assert build_dsd_metadata_map(_catalog(local)) == {}


def test_ordering_and_bounded_round_trips_from_lake() -> None:
    with LocalRegistry(":memory:") as local:
        dsd, flow = _seed_flow(local, dsd_id="DSD_D", flow_id="DF_D")
        info_msd = MetadataStructure(
            name={"en": "MSD_REF_DATAFLOW_INFO"}, id="MSD_REF_DATAFLOW_INFO", maintainer="TEST"
        )
        plain_msd = MetadataStructure(name={"en": "MSD_QUALITY"}, id="MSD_QUALITY", maintainer="TEST")
        info_mdf = Metadataflow(name={"en": "MDF_INFO"}, id="MDF_INFO", maintainer="TEST", structure=info_msd)
        plain_mdf = Metadataflow(name={"en": "MDF_QUALITY"}, id="MDF_QUALITY", maintainer="TEST", structure=plain_msd)
        for art in (info_msd, plain_msd, info_mdf, plain_mdf):
            local.add(art)
        local.add(
            MetadataSet(
                name={"en": "MDS_INFO"},
                id="MDS_INFO",
                maintainer="TEST",
                structure=info_mdf,
                targets=[artefact_urn(flow)],
            )
        )
        local.add(
            MetadataSet(
                name={"en": "MDS_QUAL"},
                id="MDS_QUAL",
                maintainer="TEST",
                structure=plain_mdf,
                targets=[artefact_urn(flow)],
            )
        )

        counting = _CountingConn(local.connection)
        md_map = build_dsd_metadata_map(_catalog(local), conn=counting)  # type: ignore[arg-type]

        # Fixed round-trips regardless of DSD / target count — no per-item SQL.
        assert counting.execute_calls == NUM_QUERIES
        titles = [link.msd_urn for link in md_map[str(artefact_urn(dsd))]]
        assert titles == [str(artefact_urn(info_msd)), str(artefact_urn(plain_msd))]


# ── Derivation from declared (structural) Metadataflow / MPA targets ─────


def _flow_urn(agency: str, id_: str, version: str) -> str:
    # Built as a raw string so wildcards ('*') are allowed — SdmxUrn.make
    # validates and rejects '*', but declared targets are stored verbatim.
    return f"urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow={agency}:{id_}({version})"


def _dsd_target_urn(agency: str, id_: str, version: str) -> str:
    return f"urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure={agency}:{id_}({version})"


def test_derives_via_declared_metadataflow_target_exact_no_set() -> None:
    # No MetadataSet at all — the association comes purely from the flow's
    # declared target.
    with LocalRegistry(":memory:") as local:
        dsd, flow = _seed_flow(local, dsd_id="DSD_E", flow_id="DF_E")
        msd = MetadataStructure(name={"en": "MSD_E"}, id="MSD_E", maintainer="TEST")
        mdf = Metadataflow(
            name={"en": "MDF_E"}, id="MDF_E", maintainer="TEST", structure=msd, targets=(str(artefact_urn(flow)),)
        )
        local.add(msd)
        local.add(mdf)

        md_map = build_dsd_metadata_map(_catalog(local))

        links = md_map[str(artefact_urn(dsd))]
        assert [link.msd_urn for link in links] == [str(artefact_urn(msd))]
        assert links[0].metadataflow_urn == str(artefact_urn(mdf))
        assert links[0].set_urns == ()  # declared, not populated


@pytest.mark.parametrize(
    "target",
    [
        _flow_urn("TEST", "DF_W", "*"),  # version wildcard
        _flow_urn("TEST", "*", "*"),  # id + version wildcard
        _flow_urn("*", "*", "*"),  # fully open
    ],
)
def test_derives_via_declared_wildcard_target(target: str) -> None:
    with LocalRegistry(":memory:") as local:
        dsd, _flow = _seed_flow(local, dsd_id="DSD_W", flow_id="DF_W")
        msd = MetadataStructure(name={"en": "MSD_W"}, id="MSD_W", maintainer="TEST")
        mdf = Metadataflow(name={"en": "MDF_W"}, id="MDF_W", maintainer="TEST", structure=msd, targets=(target,))
        local.add(msd)
        local.add(mdf)

        md_map = build_dsd_metadata_map(_catalog(local))

        assert [link.msd_urn for link in md_map[str(artefact_urn(dsd))]] == [str(artefact_urn(msd))]


def test_declared_datastructure_target_identifies_dsd_directly() -> None:
    with LocalRegistry(":memory:") as local:
        dsd, _flow = _seed_flow(local, dsd_id="DSD_DS", flow_id="DF_DS")
        msd = MetadataStructure(name={"en": "MSD_DS"}, id="MSD_DS", maintainer="TEST")
        # Target the DSD itself (wildcarded version), not the dataflow.
        target = _dsd_target_urn("TEST", "DSD_DS", "*")
        mdf = Metadataflow(name={"en": "MDF_DS"}, id="MDF_DS", maintainer="TEST", structure=msd, targets=(target,))
        local.add(msd)
        local.add(mdf)

        md_map = build_dsd_metadata_map(_catalog(local))

        assert [link.msd_urn for link in md_map[str(artefact_urn(dsd))]] == [str(artefact_urn(msd))]


def test_declared_mpa_target_two_hop() -> None:
    with LocalRegistry(":memory:") as local:
        dsd, flow = _seed_flow(local, dsd_id="DSD_MPAT", flow_id="DF_MPAT")
        msd = MetadataStructure(name={"en": "MSD_MPAT"}, id="MSD_MPAT", maintainer="TEST")
        mdf = Metadataflow(name={"en": "MDF_MPAT"}, id="MDF_MPAT", maintainer="TEST", structure=msd)
        mpa = MetadataProvisionAgreement(
            name={"en": "MPA_MPAT"},
            id="MPA_MPAT",
            maintainer="TEST",
            metadataflow=mdf,
            # Wildcard-capable URN strings since #362, not Refs.
            targets=(str(artefact_urn(flow)),),
        )
        for art in (msd, mdf, mpa):
            local.add(art)

        links = build_dsd_metadata_map(_catalog(local))[str(artefact_urn(dsd))]
        assert [link.msd_urn for link in links] == [str(artefact_urn(msd))]
        assert links[0].provision_urn == str(artefact_urn(mpa))


def test_non_matching_wildcard_yields_nothing() -> None:
    with LocalRegistry(":memory:") as local:
        _seed_flow(local, dsd_id="DSD_NM", flow_id="DF_NM")
        msd = MetadataStructure(name={"en": "MSD_NM"}, id="MSD_NM", maintainer="TEST")
        # Wildcard for a different agency — matches no dataflow in the lake.
        mdf = Metadataflow(
            name={"en": "MDF_NM"},
            id="MDF_NM",
            maintainer="TEST",
            structure=msd,
            targets=(_flow_urn("OTHER", "*", "*"),),
        )
        local.add(msd)
        local.add(mdf)

        assert build_dsd_metadata_map(_catalog(local)) == {}


def test_populated_and_declared_paths_dedupe_by_msd() -> None:
    # Same MSD reached via both a populated set and the flow's declared target
    # → a single link, with the populated set URN retained.
    with LocalRegistry(":memory:") as local:
        dsd, flow = _seed_flow(local, dsd_id="DSD_DUP", flow_id="DF_DUP")
        msd = MetadataStructure(name={"en": "MSD_DUP"}, id="MSD_DUP", maintainer="TEST")
        mdf = Metadataflow(
            name={"en": "MDF_DUP"},
            id="MDF_DUP",
            maintainer="TEST",
            structure=msd,
            targets=(_flow_urn("TEST", "DF_DUP", "*"),),
        )
        local.add(msd)
        local.add(mdf)
        local.add(
            MetadataSet(
                name={"en": "MDS_DUP"}, id="MDS_DUP", maintainer="TEST", structure=mdf, targets=[artefact_urn(flow)]
            )
        )

        links = build_dsd_metadata_map(_catalog(local))[str(artefact_urn(dsd))]
        assert len(links) == 1
        assert links[0].set_urns  # the populated set is retained


# ── Storage round-trip of declared targets ───────────────────────────────


def test_metadataflow_targets_round_trip_through_storage() -> None:
    cur = memory_cursor()
    msd = MetadataStructure(name={"en": "MSD_RT"}, id="MSD_RT", maintainer="TEST")
    targets = (_flow_urn("TEST", "DF_RT", "*"), _flow_urn("ESTAT", "CPI", "1.0"))
    mdf = Metadataflow(name={"en": "MDF_RT"}, id="MDF_RT", maintainer="TEST", structure=msd, targets=targets)
    put_artefact(cur, msd)
    put_artefact(cur, mdf)

    back = get_artefact(cur, str(artefact_urn(mdf)))
    assert isinstance(back, Metadataflow)
    assert back.targets == targets


def test_mpa_targets_round_trip_through_storage() -> None:
    cur = memory_cursor()
    msd = MetadataStructure(name={"en": "MSD_RTM"}, id="MSD_RTM", maintainer="TEST")
    mdf = Metadataflow(name={"en": "MDF_RTM"}, id="MDF_RTM", maintainer="TEST", structure=msd)
    target_urn = Dataflow.urn_for(agency="TEST", id="DF_RTM", version="1.0")
    mpa = MetadataProvisionAgreement(
        name={"en": "MPA_RTM"},
        id="MPA_RTM",
        maintainer="TEST",
        metadataflow=mdf,
        targets=(str(target_urn),),
    )
    for art in (msd, mdf, mpa):
        put_artefact(cur, art)

    back = get_artefact(cur, str(artefact_urn(mpa)))
    assert isinstance(back, MetadataProvisionAgreement)
    assert list(back.targets) == [str(target_urn)]


# ── #191: native SDMX 3.0 rel="metadata" links ───────────────────────────


def test_build_metadata_target_links_maps_target_to_reports() -> None:
    with LocalRegistry(":memory:") as local:
        _dsd, flow = _seed_flow(local, dsd_id="DSD_L", flow_id="DF_L")
        msd = MetadataStructure(name={"en": "MSD_L"}, id="MSD_L", maintainer="TEST")
        mdf = Metadataflow(name={"en": "MDF_L"}, id="MDF_L", maintainer="TEST", structure=msd)
        mds = MetadataSet(
            name={"en": "MDS_L"}, id="MDS_L", maintainer="TEST", structure=mdf, targets=[artefact_urn(flow)]
        )
        for art in (msd, mdf, mds):
            local.add(art)

        links = build_metadata_target_links(_catalog(local))

        assert links == {str(artefact_urn(flow)): [str(artefact_urn(mds))]}


def test_json_writer_emits_native_metadata_link_matching_fmr_shape() -> None:
    # Shape verified live against FMR: rel="metadata", urn=MetadataSet URN,
    # href=/metadata/metadataset/{agency}/{id}/{version}, on the target.
    flow = Dataflow(
        name={"en": "DF_L"},
        id="DF_L",
        maintainer="TEST",
        structure=DataStructure(name={"en": "DSD_L"}, id="DSD_L", maintainer="TEST"),
    )
    set_urn = str(MetadataSet.urn_for(agency="TEST", id="MDS_L", version="1.0"))
    msg = StructureMessage(dataflows=[flow])

    out = wjson.write(
        msg,
        metadata_links={str(artefact_urn(flow)): [set_urn]},
        base_url="https://host/FusionRegistry/sdmx/v2",
    )

    df = json.loads(out)["data"]["dataflows"][0]
    # The self-URN link comes first on every artefact now (#406); the
    # back-link is appended after it, which is what this test is about.
    assert df["links"] == [
        {"rel": "self", "urn": str(artefact_urn(flow))},
        {
            "rel": "metadata",
            "href": "https://host/FusionRegistry/sdmx/v2/metadata/metadataset/TEST/MDS_L/1.0",
            "urn": set_urn,
        },
    ]


def test_json_writer_emits_no_back_links_by_default() -> None:
    """Was `test_json_writer_omits_links_by_default`, and the rename is the point.

    `links` is no longer omitted: every artefact carries its own URN under
    `rel="self"` (#406). What this guards is unchanged and still worth
    guarding — a `rel="metadata"` back-link appears only when the caller asks
    for one — so the assertion moved from "no links array" to "nothing in it
    but the identity".
    """
    flow = Dataflow(name={"en": "DF_N"}, id="DF_N", maintainer="TEST")
    df = json.loads(wjson.write(StructureMessage(dataflows=[flow])))["data"]["dataflows"][0]
    assert df["links"] == [{"rel": "self", "urn": str(artefact_urn(flow))}]


def test_metadata_links_without_base_url_raises() -> None:
    flow = Dataflow(name={"en": "DF_X"}, id="DF_X", maintainer="TEST")
    with pytest.raises(ValueError, match="base_url"):
        wjson.write(StructureMessage(dataflows=[flow]), metadata_links={str(artefact_urn(flow)): ["u"]})


def test_native_link_derivation_and_emission_end_to_end() -> None:
    with LocalRegistry(":memory:") as local:
        _dsd, flow = _seed_flow(local, dsd_id="DSD_E2E", flow_id="DF_E2E")
        msd = MetadataStructure(name={"en": "MSD_E2E"}, id="MSD_E2E", maintainer="TEST")
        mdf = Metadataflow(name={"en": "MDF_E2E"}, id="MDF_E2E", maintainer="TEST", structure=msd)
        mds = MetadataSet(
            name={"en": "MDS_E2E"}, id="MDS_E2E", maintainer="TEST", structure=mdf, targets=[artefact_urn(flow)]
        )
        for art in (msd, mdf, mds):
            local.add(art)

        target_links = build_metadata_target_links(_catalog(local))
        out = wjson.write(StructureMessage(dataflows=[flow]), metadata_links=target_links, base_url="https://h/sdmx/v2")

        # By `rel`, not by position: the self-URN link is first on every
        # artefact since #406, and a positional index would make this test
        # about link ordering rather than about the back-link it names.
        links = json.loads(out)["data"]["dataflows"][0]["links"]
        link = next(entry for entry in links if entry["rel"] == "metadata")
        assert link["urn"] == str(artefact_urn(mds))
        assert link["href"].endswith("/metadata/metadataset/TEST/MDS_E2E/1.0.0")


_ML30_NS = {
    "com": "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common",
    "str": "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure",
}


def test_ml30_writer_emits_native_metadata_link_matching_fmr_shape() -> None:
    # Shape verified live against FMR: <com:Link rel="metadata" url=... urn=.../>
    # after <com:Annotations>, before <com:Name>; no `type` attribute.
    flow = Dataflow(
        id="DF_ML",
        maintainer="TEST",
        name={"en": "Flow"},
        structure=DataStructure(name={"en": "DSD_ML"}, id="DSD_ML", maintainer="TEST"),
    )
    set_urn = str(MetadataSet.urn_for(agency="TEST", id="MDS_ML", version="1.0"))
    msg = StructureMessage(dataflows=[flow])

    written = w30.write(
        msg,
        metadata_links={str(artefact_urn(flow)): [set_urn]},
        base_url="https://host/FusionRegistry/sdmx/v2",
    )
    assert_valid_sdmx_ml(written, structure_schema("3.0"))  # Link must validate in place

    root = etree.fromstring(written)
    link = root.find(".//str:Dataflow/com:Link", _ML30_NS)
    assert link is not None
    assert link.get("rel") == "metadata"
    assert link.get("urn") == set_urn
    assert link.get("url") == "https://host/FusionRegistry/sdmx/v2/metadata/metadataset/TEST/MDS_ML/1.0"
    assert link.get("type") is None  # FMR emits no type attribute

    # Positioned before <com:Name> (MaintainableBaseType ordering).
    df = root.find(".//str:Dataflow", _ML30_NS)
    assert df is not None
    tags = [etree.QName(c).localname for c in df]
    assert tags.index("Link") < tags.index("Name")


def test_ml30_writer_omits_links_by_default() -> None:
    flow = Dataflow(
        name={"en": "DF_MLN"},
        id="DF_MLN",
        maintainer="TEST",
        structure=DataStructure(name={"en": "DSD_MLN"}, id="DSD_MLN", maintainer="TEST"),
    )
    root = etree.fromstring(w30.write(StructureMessage(dataflows=[flow])))
    assert root.find(".//str:Dataflow/com:Link", _ML30_NS) is None


def test_ml30_metadata_links_without_base_url_raises() -> None:
    flow = Dataflow(name={"en": "DF_MLX"}, id="DF_MLX", maintainer="TEST")
    with pytest.raises(ValueError, match="base_url"):
        w30.write(StructureMessage(dataflows=[flow]), metadata_links={str(artefact_urn(flow)): ["u"]})


# ── End-to-end: projection survives all three writers ────────────────────


@pytest.mark.parametrize(
    ("version", "reader", "writer"),
    [("2.1", r21, w21), ("3.0", r30, w30)],
)
def test_metadata_annotation_round_trips_through_ml_writers(version: str, reader, writer) -> None:
    ml_dir = "sdmx_ml21" if version == "2.1" else "sdmx_ml30"
    msg = reader.read((_FIXTURES / ml_dir / "minimal.xml").read_bytes())
    dsd = next(iter(msg.data_structures))
    msd = _msd_urn("MSD_REF_DATAFLOW_INFO")

    out = annotate_structure_message(msg, {str(artefact_urn(dsd)): [DsdMetadataLink(msd_urn=msd)]})
    written = writer.write(out)
    assert_valid_sdmx_ml(written, structure_schema(version))  # catches ordering / conformance bugs
    back = reader.read(written)

    ann = next(iter(back.data_structures)).annotations.first(METADATA_ANNOTATION_TYPE)
    assert ann is not None
    assert ann.title == msd


def test_metadata_annotation_round_trips_through_json_writer() -> None:
    msg = rjson.read((_FIXTURES / "sdmx_json" / "minimal.json").read_bytes())
    dsd = next(iter(msg.data_structures))
    msd = _msd_urn("MSD_REF_DATAFLOW_INFO")

    out = annotate_structure_message(msg, {str(artefact_urn(dsd)): [DsdMetadataLink(msd_urn=msd)]})
    back = rjson.read(wjson.write(out))

    ann = next(iter(back.data_structures)).annotations.first(METADATA_ANNOTATION_TYPE)
    assert ann is not None
    assert ann.title == msd
