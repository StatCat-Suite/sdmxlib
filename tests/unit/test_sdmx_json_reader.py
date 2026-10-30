"""Unit tests for SDMX-JSON reader."""

from pathlib import Path

import msgspec
import pytest

from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_json.reader import read
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, AttributeUsage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support.roundtrip import as_structure, load_json

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_json"

_FIXTURE_NAMES = ["minimal", "with_groups", "with_categories"]

#: What each fixture holds, exactly. `>= 1` stood here and passed while
#: `with_groups` lost two of its three codelists — the shape #403 is about: a
#: bound on a quantity that is perfectly knowable, so the guard survives its
#: subject changing underneath it.
_EXPECTED: dict[str, dict[str, int]] = {
    "minimal": {"codelists": 1, "concept_schemes": 1, "data_structures": 1, "dataflows": 1},
    "with_groups": {"codelists": 3, "concept_schemes": 1, "data_structures": 1, "dataflows": 1},
    "with_categories": {"codelists": 2, "concept_schemes": 1, "data_structures": 1, "dataflows": 2},
}


# ── format detection via as_structure(parse()) ─────────────────────────────────────────────


def test_parse_detects_json():
    msg = as_structure(parse((_FIXTURES / "minimal.json").read_bytes()))
    assert msg.codelists.ids() == ["CL_FREQ"]


def test_parse_json_hint():
    msg = as_structure(parse((_FIXTURES / "minimal.json").read_bytes(), version="json"))
    assert msg.codelists.ids() == ["CL_FREQ"]


# ── codelists ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_codelist_ids(fixture: str):
    msg = load_json(fixture)
    assert len(msg.codelists) == _EXPECTED[fixture]["codelists"]


def test_codelist_basic():
    cl = load_json("minimal").codelists["SDMX:CL_FREQ"]
    assert cl.id == "CL_FREQ"
    assert cl.agency_id == "SDMX"
    assert cl.version == "1.0"
    assert cl.name["en"].text == "Frequency"
    # SDMX-JSON 2.1 carries no isFinal — defaults to False on read.
    assert cl.is_final is False


def test_codelist_codes():
    cl = load_json("minimal").codelists["SDMX:CL_FREQ"]
    assert cl.codes.ids() == ["A", "Q", "M"]


def test_code_name():
    code = load_json("minimal").codelists["SDMX:CL_FREQ"].codes["A"]
    assert code.name["en"].text == "Annual"


def test_code_multilingual():
    cl = load_json("with_categories").codelists["SDMX:CL_AREA"]
    eu = cl.codes["EU"]
    assert eu.name["en"].text == "European Union"
    assert eu.name["fr"].text == "Union européenne"


def test_codelist_multiple():
    msg = load_json("with_groups")
    assert set(msg.codelists.ids()) == {"CL_FREQ", "CL_AREA", "CL_CONF_STATUS"}


# ── concept schemes ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_concept_scheme_present(fixture: str):
    msg = load_json(fixture)
    assert len(msg.concept_schemes) == _EXPECTED[fixture]["concept_schemes"]


def test_concept_scheme_id():
    cs = load_json("minimal").concept_schemes["SDMX:CROSS_DOMAIN_CONCEPTS"]
    assert cs.id == "CROSS_DOMAIN_CONCEPTS"
    assert cs.agency_id == "SDMX"


def test_concept_ids():
    cs = load_json("minimal").concept_schemes["SDMX:CROSS_DOMAIN_CONCEPTS"]
    assert "FREQ" in cs.concepts.ids()
    assert "TIME_PERIOD" in cs.concepts.ids()
    assert "OBS_VALUE" in cs.concepts.ids()


# ── data structures ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_dsd_present(fixture: str):
    msg = load_json(fixture)
    assert len(msg.data_structures) == _EXPECTED[fixture]["data_structures"]


def test_dsd_id():
    dsd = load_json("minimal").data_structures[0]
    assert dsd.id == "DSD_TEST"
    assert dsd.agency_id == "TEST"
    assert dsd.version == "1.0"
    assert dsd.name["en"].text == "Test Data Structure"


def test_dimension_ids():
    dsd = load_json("minimal").data_structures[0]
    assert dsd.dimensions.ids() == ["FREQ"]


def test_dimension_order():
    dsd = load_json("minimal").data_structures[0]
    assert dsd.dimensions.ids()[0] == "FREQ"


def test_dimension_enumerated():
    dim = load_json("minimal").data_structures[0].dimensions["FREQ"]
    assert isinstance(dim.representation, Ref)
    assert dim.representation.resolved
    assert isinstance(dim.representation, Ref)
    assert dim.representation().id == "CL_FREQ"


def test_dimension_concept_resolved():
    dim = load_json("minimal").data_structures[0].dimensions["FREQ"]
    assert dim.concept is not None
    assert dim.concept.resolved
    assert dim.concept().id == "FREQ"


def test_time_dimension():
    dsd = load_json("minimal").data_structures[0]
    assert dsd.time_dimension is not None
    assert dsd.time_dimension.id == "TIME_PERIOD"
    assert isinstance(dsd.time_dimension.representation, Facet)
    assert dsd.time_dimension.representation.type == "ObservationalTimePeriod"


def test_attribute_ids():
    dsd = load_json("minimal").data_structures[0]
    assert "OBS_STATUS" in dsd.attributes.ids()


def test_attribute_usage():
    attr = load_json("minimal").data_structures[0].attributes["OBS_STATUS"]
    assert attr.usage is AttributeUsage.OPTIONAL


def test_attribute_attachment_observation():
    attr = load_json("minimal").data_structures[0].attributes["OBS_STATUS"]
    assert attr.attachment == AttachmentLevel.OBSERVATION


def test_attribute_attachment_dataset():
    attr = load_json("with_groups").data_structures[0].attributes["DECIMALS"]
    assert attr.attachment == AttachmentLevel.DATASET


def test_measure_ids():
    dsd = load_json("minimal").data_structures[0]
    assert "OBS_VALUE" in dsd.measures.ids()


def test_multiple_measures():
    dsd = load_json("with_groups").data_structures[0]
    assert dsd.measures.ids() == ["OBS_VALUE", "OBS_VALUE2"]


def test_multiple_dimensions():
    dsd = load_json("with_groups").data_structures[0]
    assert dsd.dimensions.ids() == ["FREQ", "REF_AREA"]


# ── groups ────────────────────────────────────────────────────────────────────


def test_groups_present():
    dsd = load_json("with_groups").data_structures[0]
    assert dsd.groups is not None
    assert dsd.groups.ids() == ["AreaGroup"]


def test_group_dimension_ids():
    grp = load_json("with_groups").data_structures[0].groups["AreaGroup"]  # type: ignore[index]
    assert grp.dimension_ids == ["FREQ", "REF_AREA"]


def test_no_groups_is_none():
    dsd = load_json("minimal").data_structures[0]
    assert dsd.groups is None


# ── dataflows ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", _FIXTURE_NAMES)
def test_dataflow_present(fixture: str):
    msg = load_json(fixture)
    assert len(msg.dataflows) == _EXPECTED[fixture]["dataflows"]


def test_dataflow_id():
    df = load_json("minimal").dataflows["TEST:DF_TEST"]
    assert df.id == "DF_TEST"
    assert df.agency_id == "TEST"


def test_dataflow_structure_resolved():
    df = load_json("minimal").dataflows["TEST:DF_TEST"]
    assert df.structure is not None
    assert df.structure.resolved
    assert df.structure().id == "DSD_TEST"


def test_dataflow_multilingual_name():
    df = load_json("with_categories").dataflows["TEST:DF_PRICES"]
    assert df.name["en"].text == "Price Statistics Dataflow"
    assert df.name["fr"].text == "Statistiques des prix"


def test_multiple_dataflows():
    msg = load_json("with_categories")
    assert set(msg.dataflows.ids()) == {"DF_PRICES", "DF_OUTPUT"}


# ── category schemes ──────────────────────────────────────────────────────────


def test_category_scheme_present():
    msg = load_json("with_categories")
    assert len(msg.category_schemes) == 1


def test_category_scheme_id():
    cs = load_json("with_categories").category_schemes["TEST:DATAFLOWS"]
    assert cs.id == "DATAFLOWS"


def test_top_level_categories():
    cs = load_json("with_categories").category_schemes["TEST:DATAFLOWS"]
    assert cs.categories.ids() == ["MACRO", "FINANCIAL"]


def test_nested_categories():
    cs = load_json("with_categories").category_schemes["TEST:DATAFLOWS"]
    macro = cs.categories["MACRO"]
    assert macro.categories.ids() == ["PRICES", "OUTPUT"]


# ── categorisations ───────────────────────────────────────────────────────────


def test_categorisations_present():
    msg = load_json("with_categories")
    assert len(msg.categorisations) == 2


def test_categorisation_ids():
    msg = load_json("with_categories")
    assert set(msg.categorisations.ids()) == {"CAT_PRICES", "CAT_OUTPUT"}


def test_categorisation_source_resolved():
    cat = load_json("with_categories").categorisations["TEST:CAT_PRICES"]
    assert cat.source is not None
    assert cat.source.resolved
    resolved = cat.source()
    assert isinstance(resolved, Dataflow)
    assert resolved.id == "DF_PRICES"


# ── empty message ─────────────────────────────────────────────────────────────


def test_empty_json():
    msg = read(b'{"meta": {}, "data": {}}')
    assert msg.codelists.ids() == []
    assert msg.data_structures.ids() == []
    assert msg.dataflows.ids() == []


# ── envelope typing (#200) ───────────────────────────────────────────────────


def test_absent_data_key_yields_empty_message():
    """No ``data`` key at all is tolerated, matching a bare ``meta`` response."""
    msg = read(b'{"meta": {"id": "X"}}')
    assert msg.codelists.ids() == []


def test_explicit_null_data_yields_empty_message():
    msg = read(b'{"data": null}')
    assert msg.codelists.ids() == []


def test_unknown_section_is_ignored():
    """A producer emitting a section sdmxlib does not model must not be fatal."""
    msg = read(b'{"data": {"somethingNew": [1, 2, 3], "codelists": []}}')
    assert msg.codelists.ids() == []


@pytest.mark.parametrize(
    "blob",
    [
        pytest.param(b"[1, 2]", id="root-is-a-list"),
        pytest.param(b'{"data": [1, 2]}', id="data-is-a-list"),
    ],
)
def test_malformed_envelope_raises_validation_error(blob: bytes):
    """A non-object envelope is a validation failure, not an AttributeError.

    Before the envelope was typed these decoded untyped and then called
    ``.get`` on a list, surfacing ``AttributeError: 'list' object has no
    attribute 'get'`` — an internal leak rather than a message about the input.
    """
    with pytest.raises(msgspec.ValidationError):
        read(blob)


def test_a_malformed_entry_is_refused_naming_its_artefact_and_field():
    """A malformed entry is refused alone, located by its class and identity (#474).

    It used to fail the whole document with msgspec's path to it. The refusal
    locates it better: by the artefact it would have been, and the field.
    Asserts on the *field name* — ours — rather than on msgspec's ``$.``
    path syntax, which a version bump could reword.
    """
    ok = b'{"id": "OK", "agencyID": "AG", "version": "1.0", "name": "ok", "names": {"en": "ok"}}'
    msg = read(b'{"data": {"codelists": [{"id": 5, "agencyID": "AG", "version": "1.0"}, ' + ok + b"]}}")
    (refusal,) = msg.refused
    assert refusal.artefact_class == "Codelist"
    assert "=AG:" in refusal.urn
    assert "id" in refusal.reason, f"refusal does not name the field: {refusal.reason}"
    assert msg.codelists.ids() == ["OK"], "the rest of the section is read"
