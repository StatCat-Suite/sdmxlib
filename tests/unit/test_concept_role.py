"""Tests for concept_role on Dimension / Measure / Attribute (SDMX 3.0 IM §5.3)."""

import duckdb
import pytest

from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml30.writer import write as write30
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.concept import Concept
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Measure,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_data_structure
from tests.support.factories import concept_ref
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit


def _cl_urn() -> SdmxUrn[Codelist]:
    return SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_FREQ",
        version="1.0",
    )


def _dsd_with_roles() -> DataStructure:
    cl = _cl_urn()
    concept_urn = Concept.urn_for(agency="SDMX", scheme="CS_COMMON", id="FREQ")
    return DataStructure(
        id="DSD_ROLES",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Roles DSD"},
        dimensions=[
            Dimension(
                id="FREQ",
                concept=Ref[Concept].unresolved(concept_urn),
                representation=Ref[Codelist].unresolved(cl),
                concept_role=("FREQUENCY",),
            ),
            Dimension(
                id="STAT_MEASURE",
                concept=Ref[Concept].unresolved(concept_urn),
                representation=Ref[Codelist].unresolved(cl),
                concept_role=("MEASURE",),
            ),
            Dimension(id="PLAIN", concept=Ref[Concept].unresolved(concept_urn)),
        ],
        attributes=[
            Attribute(
                id="OBS_STATUS",
                concept=Ref[Concept].unresolved(concept_urn),
                attachment=AttachmentLevel.OBSERVATION,
                concept_role=("ENTITY", "COUNT"),
            ),
        ],
        measures=[
            Measure(id="OBS_VALUE", concept=Ref[Concept].unresolved(concept_urn), concept_role=("MEASURE",)),
        ],
    )


# ── model: defaults + converter ──────────────────────────────────────────────


def test_default_concept_role_is_empty_tuple():
    assert Dimension(id="X", concept=concept_ref("X")).concept_role == ()
    assert Measure(id="Y", concept=concept_ref("Y")).concept_role == ()
    assert Attribute(attachment=AttachmentLevel.DATASET, id="Z", concept=concept_ref("Z")).concept_role == ()


def test_concept_role_converter_accepts_list():
    dim = Dimension(id="X", concept_role=["MEASURE", "FREQUENCY"], concept=concept_ref("X"))
    assert dim.concept_role == ("MEASURE", "FREQUENCY")


def test_concept_role_converter_accepts_none():
    dim = Dimension(id="X", concept_role=None, concept=concept_ref("X"))
    assert dim.concept_role == ()


# ── SDMX-ML 3.0 round-trip ───────────────────────────────────────────────────


def _ml30_roundtrip(dsd: DataStructure) -> DataStructure:
    msg = StructureMessage(data_structures=ArtefactList([dsd]))
    return as_structure(parse(write30(msg))).data_structures[dsd.id]


def test_ml30_roundtrip_dimension_role():
    rt = _ml30_roundtrip(_dsd_with_roles())
    assert rt.dimensions["FREQ"].concept_role == ("FREQUENCY",)
    assert rt.dimensions["STAT_MEASURE"].concept_role == ("MEASURE",)
    assert rt.dimensions["PLAIN"].concept_role == ()


def test_ml30_roundtrip_attribute_role_multi():
    rt = _ml30_roundtrip(_dsd_with_roles())
    assert rt.attributes["OBS_STATUS"].concept_role == ("ENTITY", "COUNT")


def test_ml30_roundtrip_measure_role():
    rt = _ml30_roundtrip(_dsd_with_roles())
    assert rt.measures["OBS_VALUE"].concept_role == ("MEASURE",)


def test_ml30_emits_concept_role_element():
    data = write30(StructureMessage(data_structures=ArtefactList([_dsd_with_roles()])))
    assert b"ConceptRole" in data


# ── DuckDB storage round-trip ────────────────────────────────────────────────


def test_storage_roundtrip_concept_role():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    put_data_structure(cursor, _dsd_with_roles())
    urn = SdmxUrn.make(
        DataStructure,
        package="datastructure",
        artefact_class="DataStructure",
        agency="SDMX",
        id="DSD_ROLES",
        version="1.0",
    )
    rt = get_artefact(cursor, str(urn))
    assert isinstance(rt, DataStructure)
    assert rt is not None
    assert rt.dimensions["FREQ"].concept_role == ("FREQUENCY",)
    assert rt.dimensions["STAT_MEASURE"].concept_role == ("MEASURE",)
    assert rt.dimensions["PLAIN"].concept_role == ()
    assert rt.attributes["OBS_STATUS"].concept_role == ("ENTITY", "COUNT")
    assert rt.measures["OBS_VALUE"].concept_role == ("MEASURE",)
