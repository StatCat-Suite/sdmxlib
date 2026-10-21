"""Regression tests for the serializer round-trip audit (#159).

Each test pins one previously-asymmetric field drop: a model field that
one side of a reader/writer (or storage writer/reader) pair handled while
the other silently dropped it. Grouped by the fix in issue #159's Table A.
"""

import functools
from datetime import UTC, datetime

import duckdb
import pytest
from lxml import etree

from sdmxlib.formats.sdmx_json import metadata as json_md
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import metadata as ml30_md
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, MeasureDimension
from sdmxlib.model.mapping import RepresentationMap, RepresentationMapping, SourceValue
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    DataProvider,
    DataProviderScheme,
)
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_codelist, put_data_structure
from tests.support.factories import concept_ref
from tests.support.narrow import present
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit


@functools.cache
def _schema_21() -> etree.XMLSchema:
    return compile_structure_schema("2.1")


# ── SDMX-JSON does not carry isFinal ─────────────────────────────────────────


def test_json_never_emits_is_final() -> None:
    """Reversed by #358, and the reversal is the interesting part.

    #159 added `isFinal` to the SDMX-JSON writer and reader on the reasoning
    that the model carried it and the format could hold it. The format cannot:
    SDMX-JSON encodes the SDMX 3.0 information model, which removed `isFinal`
    from every maintainable, and `isFinal` appears **nowhere** in the official
    structure schema.

    It is not, today, an *invalid* document: SDMX-JSON **2.0.0** — what this
    library emits — leaves objects open, and one carrying `isFinal` validates
    with zero errors. **2.1.0 closes them** (`unevaluatedProperties: false`),
    where the same key fails outright:

        data.codelists.0: Unevaluated properties are not allowed
                          ('isFinal' was unexpected)

    So this is removed for what it costs on the 2.1 upgrade rather than for
    what it breaks now — a key the standard never defined, written for no
    reader but this one.
    """
    import msgspec

    cl = Codelist(
        id="CL",
        maintainer="T",
        version="1.0",
        name={"en": "X"},
        legacy_is_final=True,
        codes=[Code(name={"en": "A"}, id="A")],
    )
    written = json_writer.write(StructureMessage(codelists=[cl]))
    assert b"isFinal" not in written
    assert "isFinal" not in msgspec.json.decode(written)["data"]["codelists"][0]

    back = json_reader.read(written).codelists["CL"]
    assert back.legacy_is_final is None, "declared a gap in gaps.py, so it comes back unset"


# ── #2: DuckDB storage valid_from / valid_to on maintainables ─────────────────


def test_storage_validity_round_trips_eager_and_lazy() -> None:
    conn = create_schema(duckdb.connect(":memory:"))
    cl = Codelist(
        id="CL",
        maintainer="T",
        version="1.0",
        name={"en": "X"},
        valid_from=datetime(2020, 1, 1),
        valid_to=datetime(2021, 1, 1),
        codes=[Code(name={"en": "A"}, id="A")],
    )
    put_codelist(conn, cl)
    urn = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=T:CL(1.0)"

    lazy = get_artefact(conn, urn)  # default path → LazyCodelist
    eager = get_artefact(conn, urn, eager=True)  # → Codelist
    # Read back tz-aware, in UTC. #321 changed this: the column has no zone, so
    # an *aware* value used to be converted to the reader's local time and
    # returned naive — a different instant per machine. The fix re-tags on
    # read, which means a naive input comes home labelled UTC. Both readings
    # cannot hold at once, and returning a shifted instant is the worse one.
    assert present(lazy).valid_from == datetime(2020, 1, 1, tzinfo=UTC)
    assert present(lazy).valid_to == datetime(2021, 1, 1, tzinfo=UTC)
    assert present(eager).valid_from == datetime(2020, 1, 1, tzinfo=UTC)
    assert present(eager).valid_to == datetime(2021, 1, 1, tzinfo=UTC)


def test_storage_validity_round_trips_data_structure() -> None:
    conn = create_schema(duckdb.connect(":memory:"))
    dsd = DataStructure(
        id="D",
        maintainer="T",
        version="1.0",
        name={"en": "D"},
        valid_from=datetime(2019, 5, 5),
        dimensions=ItemList([Dimension(id="F", concept=concept_ref("F"))]),
    )
    put_data_structure(conn, dsd)
    back = get_artefact(conn, "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=T:D(1.0)")
    # Tz-aware in UTC — storage re-tags on read (#321). The wire-format
    # round-trips further down this file are unaffected: only the zoneless
    # DuckDB column needed a convention.
    assert present(back).valid_from == datetime(2019, 5, 5, tzinfo=UTC)


# ── #3: SDMX-ML 2.1 MeasureDimension ──────────────────────────────────────────


def test_ml21_measure_dimension_round_trips_and_is_xsd_valid() -> None:
    cs_ref = Ref[ConceptScheme].unresolved(
        SdmxUrn(package="conceptscheme", artefact_class="ConceptScheme", agency="T", id="CS_MEAS", version="1.0")
    )
    dsd = DataStructure(
        id="X",
        maintainer="T",
        version="1.0",
        name={"en": "X"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"))]),
        measure_dimension=MeasureDimension(id="MEASURE", concept=concept_ref("MEAS"), representation=cs_ref),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS"))]),
    )
    written = ml21_writer.write(StructureMessage(data_structures=[dsd]))
    assert_valid_sdmx_ml(written, _schema_21())
    back = ml21_reader.read(written).data_structures["X"]
    assert back.measure_dimension is not None
    assert back.measure_dimension.id == "MEASURE"
    assert isinstance(back.measure_dimension.representation, Ref)
    assert back.measure_dimension.representation.urn.id == "CS_MEAS"


# ── #4: SDMX-JSON RepresentationMap regex / index variants ────────────────────


def test_json_representation_map_variants_round_trip() -> None:
    def cl_ref(id_: str) -> Ref[Codelist]:
        return Ref[Codelist].unresolved(
            SdmxUrn(package="codelist", artefact_class="Codelist", agency="T", id=id_, version="1.0")
        )

    rm = RepresentationMap(
        id="RM",
        maintainer="T",
        version="1.0",
        name={"en": "M"},
        sources=(cl_ref("S"),),
        targets=(cl_ref("D"),),
        mappings=(
            RepresentationMapping(sources=("A",), targets=("1",)),
            RepresentationMapping(sources=(SourceValue("X.*", is_regex=True),), targets=("2",)),
            RepresentationMapping(sources=(SourceValue("ABCD", start_index=1, end_index=3),), targets=("3",)),
        ),
    )
    back = json_reader.read(json_writer.write(StructureMessage(representation_maps=[rm]))).representation_maps["RM"]
    assert back.mappings == rm.mappings


# ── #5 / #6: MetadataSet valid_from / valid_to (ML 3.0 + JSON metadata) ────────


@pytest.mark.parametrize("mod", [ml30_md, json_md], ids=["ml30", "json"])
def test_metadata_set_validity_round_trips(mod) -> None:
    mf_urn = SdmxUrn(package="metadatastructure", artefact_class="Metadataflow", agency="T", id="MDF", version="1.0")
    ms = MetadataSet(
        name={"en": "MS"},
        id="MS",
        maintainer="T",
        version="1.0",
        structure=Ref[Metadataflow].unresolved(mf_urn),
        valid_from=datetime(2021, 3, 1),
        valid_to=datetime(2022, 3, 1),
    )
    back = mod.read(mod.write(MetadataMessage(metadata_sets=[ms]))).metadata_sets[0]
    assert back.valid_from == datetime(2021, 3, 1, tzinfo=UTC)
    assert back.valid_to == datetime(2022, 3, 1, tzinfo=UTC)


# ── #7: SDMX-ML organisation item annotations ─────────────────────────────────


def test_ml21_org_item_annotations_round_trip() -> None:
    ags = AgencyScheme(
        id="AGENCIES",
        maintainer="T",
        version="1.0",
        name={"en": "A"},
        agencies=ItemList(
            [Agency(id="T", name={"en": "T"}, annotations=Annotations([Annotation(type="AG", title="on-agency")]))]
        ),
    )
    dps = DataProviderScheme(
        id="DATA_PROVIDERS",
        maintainer="T",
        version="1.0",
        name={"en": "D"},
        providers=ItemList(
            [DataProvider(name={"en": "P"}, id="P", annotations=Annotations([Annotation(type="DP", title="on-dp")]))]
        ),
    )
    msg = StructureMessage(agency_schemes=[ags], data_provider_schemes=[dps])
    back = ml21_reader.read(ml21_writer.write(msg))
    agency = next(iter(back.agency_schemes)).agencies["T"]
    provider = next(iter(back.data_provider_schemes)).providers["P"]
    assert agency.annotations.first("AG") is not None
    assert provider.annotations.first("DP") is not None
