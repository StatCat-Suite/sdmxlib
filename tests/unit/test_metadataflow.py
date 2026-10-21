"""Tests for Metadataflow — model, storage round-trip, JSON round-trip."""

import pytest

from sdmxlib.formats.sdmx_json.reader import read as read_json
from sdmxlib.formats.sdmx_json.writer import write as write_json
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.writers import put_artefact
from tests.support.factories import footnote_metadataflow, footnote_msd, memory_cursor

pytestmark = pytest.mark.unit


_MDF_URN = "urn:sdmx:org.sdmx.infomodel.metadatastructure.Metadataflow=STATCAN:MDF_FOOTNOTES(1.0)"


# ── Model ───────────────────────────────────────────────────────────────


def test_metadataflow_constructs_with_resolved_msd_ref() -> None:
    msd = footnote_msd()
    mdf = footnote_metadataflow(msd)
    assert mdf.structure is not None
    assert mdf.structure.urn.id == "MSD_FOOTNOTES"
    assert mdf.structure.resolved
    assert mdf.structure() is msd


def test_metadataflow_accepts_unresolved_urn() -> None:
    msd_urn = MetadataStructure.urn_for(agency="STATCAN", id="MSD_FOOTNOTES", version="1.0")
    mdf = Metadataflow(name={"en": "MDF"}, id="MDF", maintainer="STATCAN", structure=msd_urn)
    assert mdf.structure is not None
    assert not mdf.structure.resolved


def test_metadataflow_allows_no_structure() -> None:
    mdf = Metadataflow(name={"en": "MDF"}, id="MDF", maintainer="STATCAN")
    assert mdf.structure is None


# ── Storage round-trip ──────────────────────────────────────────────────


def test_metadataflow_round_trip() -> None:
    cur = memory_cursor()
    msd = footnote_msd()
    mdf = footnote_metadataflow(msd)
    put_artefact(cur, msd)
    put_artefact(cur, mdf)

    out = get_artefact(cur, _MDF_URN)
    assert isinstance(out, Metadataflow)
    assert out.id == "MDF_FOOTNOTES"
    assert isinstance(out.structure, Ref)
    assert out.structure.urn.id == "MSD_FOOTNOTES"


# ── JSON round-trip ─────────────────────────────────────────────────────


def test_metadataflow_json_round_trip_resolves_in_message() -> None:
    msd = footnote_msd()
    mdf = footnote_metadataflow(msd)
    msg = StructureMessage(metadata_structures=[msd], metadataflows=[mdf])
    out = read_json(write_json(msg))

    parsed = out.metadataflows[0]
    assert parsed.id == "MDF_FOOTNOTES"
    assert isinstance(parsed.structure, Ref)
    assert parsed.structure.urn.id == "MSD_FOOTNOTES"
    assert parsed.structure.resolved
    assert parsed.structure() is out.metadata_structures[0]


# ── Registry interop ────────────────────────────────────────────────────


def test_registry_resolves_msd_ref_through_metadataflow() -> None:
    reg = InMemoryRegistry()
    msd = footnote_msd()
    mdf = footnote_metadataflow(msd)
    reg.add_all([msd, mdf])
    assert mdf.structure is not None
    assert mdf.structure.resolved
    assert mdf.structure() is msd
