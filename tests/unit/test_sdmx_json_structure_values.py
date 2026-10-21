"""Per-item SDMX-JSON value serialiser tests for Codelists / ConceptSchemes.

Covers ``sdmxlib.formats.sdmx_json.structure_values``: the canonical
per-Code and per-Concept dict shape that external producers (cache
pre-bakers) and consumers (response hydrators) share. Issue #150.
"""

import msgspec
import pytest

from sdmxlib.formats import serialize
from sdmxlib.formats.sdmx_json import (
    serialize_code,
    serialize_codes,
    serialize_concept,
    serialize_concepts,
)
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet

pytestmark = pytest.mark.unit


# ── Code ──────────────────────────────────────────────────────────────────────


def test_serialize_code_least() -> None:
    """A code's least: its id and its name, which it cannot lack (#492)."""
    assert serialize_code(Code(name={"en": "A"}, id="A")) == {"id": "A", "name": "A", "names": {"en": "A"}}


def test_serialize_code_multilang_name_emits_primary_and_full_map() -> None:
    code = Code(id="A", name=InternationalString(en="Annual", fr="Annuel"))
    out = serialize_code(code)
    assert out["id"] == "A"
    assert "names" in out
    assert out["names"] == {"en": "Annual", "fr": "Annuel"}
    # `name` is the primary fallback — must be one of the names entries.
    assert "name" in out
    assert "names" in out
    assert out["name"] in out["names"].values()


def test_serialize_code_description_emits_both_keys() -> None:
    code = Code(
        id="A",
        name=InternationalString(en="Annual"),
        description=InternationalString(en="Yearly observations"),
    )
    out = serialize_code(code)
    assert "description" in out
    assert out["description"] == "Yearly observations"
    assert "descriptions" in out
    assert out["descriptions"] == {"en": "Yearly observations"}


def test_serialize_code_parent_emitted_when_set() -> None:
    out = serialize_code(Code(name={"en": "A1"}, id="A1", parent_id="A"))
    assert "parent" in out
    assert out["parent"] == "A"


def test_serialize_code_parent_omitted_when_unset() -> None:
    assert "parent" not in serialize_code(Code(name={"en": "A"}, id="A"))


def test_serialize_codes_empty_codelist() -> None:
    cl = Codelist(id="CL_X", maintainer="TEST", version="1.0", name=InternationalString(en="X"))
    assert serialize_codes(cl) == []


def test_serialize_codes_preserves_order() -> None:
    cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(name={"en": "A"}, id="A"), Code(name={"en": "Q"}, id="Q"), Code(name={"en": "M"}, id="M")],
    )
    assert [c["id"] for c in serialize_codes(cl)] == ["A", "Q", "M"]


# ── Concept ───────────────────────────────────────────────────────────────────


def test_serialize_concept_least() -> None:
    """A concept's least: its id and its name, which it cannot lack (#492)."""
    assert serialize_concept(Concept(name={"en": "FREQ"}, id="FREQ")) == {
        "id": "FREQ",
        "name": "FREQ",
        "names": {"en": "FREQ"},
    }


def test_serialize_concept_name_and_description() -> None:
    c = Concept(
        id="FREQ",
        name=InternationalString(en="Frequency", fr="Fréquence"),
        description=InternationalString(en="Reporting frequency"),
    )
    out = serialize_concept(c)
    assert "names" in out
    assert out["names"] == {"en": "Frequency", "fr": "Fréquence"}
    assert "descriptions" in out
    assert out["descriptions"] == {"en": "Reporting frequency"}


def test_serialize_concept_core_representation_enumeration() -> None:
    cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
    )
    ref = Ref[Codelist].to(cl)
    c = Concept(name={"en": "FREQ"}, id="FREQ", core_representation=ref)
    out = serialize_concept(c)
    assert "coreRepresentation" in out
    assert out["coreRepresentation"] == {"enumeration": str(ref.urn)}


def test_serialize_concept_core_representation_facet() -> None:
    c = Concept(
        name={"en": "OBS_VALUE"},
        id="OBS_VALUE",
        core_representation=Facet(type=DataType.Double, min_value=0),
    )
    out = serialize_concept(c)
    assert "coreRepresentation" in out
    assert out["coreRepresentation"] == {"format": {"dataType": "Double", "minValue": 0}}


def test_serialize_concept_core_representation_omitted_when_none() -> None:
    assert "coreRepresentation" not in serialize_concept(Concept(name={"en": "X"}, id="X"))


def test_serialize_concepts_preserves_order() -> None:
    cs = ConceptScheme(
        id="CS",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="CS"),
        concepts=[Concept(name={"en": "A"}, id="A"), Concept(name={"en": "B"}, id="B")],
    )
    assert [c["id"] for c in serialize_concepts(cs)] == ["A", "B"]


# ── Writer parity ─────────────────────────────────────────────────────────────


def test_serialize_codes_matches_writer_envelope_codes_block() -> None:
    """The writer's DTO codes block must stay identical to ``serialize_codes``."""
    cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency", fr="Fréquence"),
        codes=[
            Code(
                id="A",
                name=InternationalString(en="Annual"),
                description=InternationalString(en="Yearly"),
            ),
            Code(name={"en": "A1"}, id="A1", parent_id="A"),
        ],
    )
    doc = msgspec.json.decode(serialize(StructureMessage(codelists=[cl]), version="json"))
    assert doc["data"]["codelists"][0]["codes"] == serialize_codes(cl)


def test_serialize_concepts_matches_writer_envelope_concepts_block() -> None:
    """The writer's DTO concepts block must stay identical to ``serialize_concepts``."""
    cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
    )
    cs = ConceptScheme(
        id="CS",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="CS"),
        concepts=[
            Concept(name={"en": "FREQ"}, id="FREQ", core_representation=Ref[Codelist].to(cl)),
            Concept(name={"en": "OBS_VALUE"}, id="OBS_VALUE", core_representation=Facet(type=DataType.Double)),
            Concept(name={"en": "PLAIN"}, id="PLAIN"),
        ],
    )
    doc = msgspec.json.decode(serialize(StructureMessage(concept_schemes=[cs]), version="json"))
    assert doc["data"]["conceptSchemes"][0]["concepts"] == serialize_concepts(cs)
