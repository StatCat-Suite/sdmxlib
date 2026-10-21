"""Round-trip acceptance tests for the 'complete' fixtures.

Each fixture is parsed → serialized → re-parsed.  Every modelled section
must survive the round-trip with its content intact.

Covers:
  - OrganisationSchemes (AgencyScheme + DataProviderScheme)
  - Codelists (including hierarchical codes)
  - ConceptScheme
  - DataStructures (dimensions, time-dimension, group, attribute, measure)
  - Dataflows
  - CategorySchemes + Categorisations
  - ProvisionAgreements
  - StructureSets / CodelistMaps
  - Constraints / CubeRegions
"""

from pathlib import Path

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_ml21.reader import read as read21
from sdmxlib.formats.sdmx_ml21.writer import write as write21
from sdmxlib.formats.sdmx_ml30.reader import read as read30
from sdmxlib.formats.sdmx_ml30.writer import write as write30
from sdmxlib.model.message import StructureMessage
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_ML21 = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21" / "complete.xml"
_ML30 = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30" / "complete.xml"


# ── helpers ───────────────────────────────────────────────────────────────────


def _roundtrip21() -> tuple[StructureMessage, StructureMessage]:
    orig = read21(_ML21.read_bytes())
    return orig, read21(write21(orig))


def _roundtrip30() -> tuple[StructureMessage, StructureMessage]:
    orig = read30(_ML30.read_bytes())
    return orig, read30(write30(orig))


# ── parse smoke ───────────────────────────────────────────────────────────────


def test_parse_ml21_complete():
    msg = as_structure(parse(_ML21.read_bytes()))
    assert msg is not None


def test_parse_ml30_complete():
    msg = as_structure(parse(_ML30.read_bytes()))
    assert msg is not None


# ── Codelists ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_codelist_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.codelists.ids() == orig.codelists.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_codelist_code_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.codelists, result.codelists, strict=True):
        assert r.codes.ids() == o.codes.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_codelist_hierarchy(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o_cl, r_cl in zip(orig.codelists, result.codelists, strict=True):
        for o_code, r_code in zip(o_cl.codes, r_cl.codes, strict=True):
            assert r_code.parent_id == o_code.parent_id


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_codelist_multilingual(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    cl_freq_orig = orig.codelists["CL_FREQ"]
    cl_freq_rt = result.codelists["CL_FREQ"]
    assert cl_freq_rt.codes["A"].name.get("en") == cl_freq_orig.codes["A"].name.get("en")
    assert cl_freq_rt.codes["A"].name.get("fr") == cl_freq_orig.codes["A"].name.get("fr")


# ── ConceptSchemes ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_concept_scheme_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.concept_schemes.ids() == orig.concept_schemes.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_concept_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.concept_schemes, result.concept_schemes, strict=True):
        assert r.concepts.ids() == o.concepts.ids()


# ── DataStructures ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_dsd_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.data_structures.ids() == orig.data_structures.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_dimension_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.data_structures, result.data_structures, strict=True):
        assert r.dimensions.ids() == o.dimensions.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_time_dimension(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.data_structures, result.data_structures, strict=True):
        if o.time_dimension is not None:
            assert r.time_dimension is not None
            assert r.time_dimension.id == o.time_dimension.id


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_group_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.data_structures, result.data_structures, strict=True):
        o_groups = o.groups
        r_groups = r.groups
        if o_groups is not None:
            assert r_groups is not None
            assert r_groups.ids() == o_groups.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_group_dimension_refs(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    dsd_orig = orig.data_structures["DSD_TEST"]
    dsd_rt = result.data_structures["DSD_TEST"]
    assert dsd_orig.groups is not None
    assert dsd_rt.groups is not None
    assert dsd_rt.groups["GRP_FREQ_AREA"].dimension_ids == dsd_orig.groups["GRP_FREQ_AREA"].dimension_ids


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_attribute_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.data_structures, result.data_structures, strict=True):
        assert r.attributes.ids() == o.attributes.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_attribute_usage(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o_dsd, r_dsd in zip(orig.data_structures, result.data_structures, strict=True):
        for o_attr, r_attr in zip(o_dsd.attributes, r_dsd.attributes, strict=True):
            assert r_attr.usage == o_attr.usage


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_measure_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.data_structures, result.data_structures, strict=True):
        assert r.measures.ids() == o.measures.ids()


# ── Dataflows ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_dataflow_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.dataflows.ids() == orig.dataflows.ids()


# ── CategorySchemes + Categorisations ────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_category_scheme_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.category_schemes.ids() == orig.category_schemes.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_nested_categories(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    cs_orig = orig.category_schemes["CS_TEST"]
    cs_rt = result.category_schemes["CS_TEST"]
    assert cs_rt.categories.ids() == cs_orig.categories.ids()
    assert cs_rt.categories["CAT_A"].categories.ids() == cs_orig.categories["CAT_A"].categories.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_categorisation_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.categorisations.ids() == orig.categorisations.ids()


# ── OrganisationSchemes ───────────────────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_agency_scheme_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.agency_schemes.ids() == orig.agency_schemes.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_agency_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.agency_schemes, result.agency_schemes, strict=True):
        assert r.agencies.ids() == o.agencies.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_data_provider_scheme_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.data_provider_schemes.ids() == orig.data_provider_schemes.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_data_provider_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    for o, r in zip(orig.data_provider_schemes, result.data_provider_schemes, strict=True):
        assert r.providers.ids() == o.providers.ids()


# ── ProvisionAgreements ───────────────────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_provision_agreement_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.provision_agreements.ids() == orig.provision_agreements.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_provision_agreement_dataflow_resolved(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    pa_orig = orig.provision_agreements["PA_TEST"]
    pa_rt = result.provision_agreements["PA_TEST"]
    assert pa_orig.dataflow is not None
    assert pa_rt.dataflow is not None
    assert pa_rt.dataflow.resolved == pa_orig.dataflow.resolved


# ── Structure mapping (2.1 only; 3.0 fixture exercised separately) ────────────
# Both versions promote maps to top-level lists on StructureMessage.
# The 2.1 writer wraps them in a synthetic <str:StructureSet>; the 3.0
# writer emits per-kind containers (RepresentationMaps, etc.). The 2.1
# complete fixture has a CodelistMap with two CodeMaps; the 3.0 complete
# fixture currently exercises no maps and is added in a follow-up.


def test_representation_map_ids_ml21():
    orig, result = _roundtrip21()
    assert result.representation_maps.ids() == orig.representation_maps.ids()


def test_representation_map_value_maps_ml21():
    orig, result = _roundtrip21()
    rm_orig = orig.representation_maps[0]
    rm_rt = result.representation_maps[0]
    assert rm_rt.mappings == rm_orig.mappings


# ── Constraints ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_constraint_ids(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    assert result.constraints.ids() == orig.constraints.ids()


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_constraint_type(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    cc_orig = orig.constraints["CC_TEST"]
    cc_rt = result.constraints["CC_TEST"]
    assert cc_rt.constraint_type == cc_orig.constraint_type


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_constraint_cube_region_count(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    cc_orig = orig.constraints["CC_TEST"]
    cc_rt = result.constraints["CC_TEST"]
    assert len(cc_rt.regions) == len(cc_orig.regions)


@pytest.mark.parametrize("rt", ["ml21", "ml30"])
def test_constraint_key_values(rt: str):
    orig, result = _roundtrip21() if rt == "ml21" else _roundtrip30()
    cc_orig = orig.constraints["CC_TEST"]
    cc_rt = result.constraints["CC_TEST"]
    orig_kvs = {kv.dimension_id: kv.values for kv in cc_orig.regions[0].keys}
    rt_kvs = {kv.dimension_id: kv.values for kv in cc_rt.regions[0].keys}
    assert rt_kvs == orig_kvs


def test_constraint_data_provider_attachment_30():
    """#130: ConstraintAttachment.data_provider round-trips through 3.0
    XML (the dataProvider variant of DataConstraintAttachmentType).

    Only exercised on 3.0 because the complete 2.1 fixture doesn't carry
    a CC_DP constraint (the 2.1 schema also supports it, but coverage is
    via the unit fixture only).
    """
    _, result = _roundtrip30()
    cc = result.constraints["CC_DP"]
    assert cc.attachment.data_provider is not None
    assert cc.attachment.data_provider.urn.item_id == "PROV_A"


# ── serialize() entrypoint ────────────────────────────────────────────────────


def test_serialize_ml21_complete():
    msg = as_structure(parse(_ML21.read_bytes()))
    out = serialize(msg, version="2.1")
    rt = as_structure(parse(out))
    assert rt.codelists.ids() == msg.codelists.ids()
    assert rt.data_structures.ids() == msg.data_structures.ids()
    assert rt.constraints.ids() == msg.constraints.ids()
    assert rt.representation_maps.ids() == msg.representation_maps.ids()
    assert rt.provision_agreements.ids() == msg.provision_agreements.ids()
