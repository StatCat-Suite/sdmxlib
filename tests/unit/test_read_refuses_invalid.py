"""A reader refuses what `Registry.add` would, and reads on (#419).

`add` refuses an artefact no SDMX format can write. A reader used to build it
anyway, so one such DSD in a registry's bulk document reached `add_all` and
failed the whole batch — 54 of IMF's 240 3.0 DSDs did, taking the other 186
with them. Now `parse` refuses it into `StructureMessage.refused`, as it
refuses an element that contradicts the schema, and what referenced it is left
unresolved.

The documents here are written by the format-internal writers, which do not
validate, because no validating writer will produce them — which is the point.
Each refusal is paired with the same document made valid, so the assertion
that a reference was withdrawn is checked against one that was resolved.
"""

from typing import Any

import httpx
import msgspec
import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml21.writer import write as write21
from sdmxlib.formats.sdmx_ml30.writer import write as write30
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.validation import SdmxConformanceError, SdmxParseError, ValidationError
from tests.support.factories import concept_ref
from tests.support.narrow import present
from tests.support.repo import find_root
from tests.support.roundtrip import as_structure
from tests.support.unchecked import unchecked

pytestmark = pytest.mark.unit

_DSD_URN = sl.DataStructure.urn_for(agency="T", id="DSD", version="1.0")


def _concept(id_: str) -> sl.Concept:
    """Built unchecked: the model refuses a bad id as it is set (#492), and the document needs one."""
    with unchecked():
        return sl.Concept(id=id_, name={"en": "n"})


def _concepts(*, valid: bool = True) -> sl.ConceptScheme:
    """`T:CS(1.0)`; with an id SDMX's `IDType` refuses, one `add` refuses."""
    return sl.ConceptScheme(
        id="CS",
        maintainer="T",
        name={"en": "cs"},
        concepts=[
            sl.Concept(id="FREQ", name={"en": "f"}),
            sl.Concept(id="OBS_VALUE", name={"en": "v"}),
            _concept("NOTE" if valid else "NOT E"),
        ],
    )


def _dsd(*, concept: bool = True) -> sl.DataStructure:
    """`T:DSD(1.0)`; without a concept on `FREQ`, built unchecked for a document to hold (#492)."""
    if not concept:
        with unchecked():
            return _dsd_built(concept=False)
    return _dsd_built(concept=True)


def _dsd_built(*, concept: bool) -> sl.DataStructure:
    return sl.DataStructure(
        id="DSD",
        maintainer="T",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ") if concept else None)],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _flow() -> sl.Dataflow:
    return sl.Dataflow(
        id="DF", maintainer="T", name={"en": "f"}, structure=sl.Ref[sl.DataStructure].unresolved(_DSD_URN)
    )


def _read(*, dsd_valid: bool = True, scheme_valid: bool = True) -> sl.StructureMessage:
    document = sl.StructureMessage(
        concept_schemes=[_concepts(valid=scheme_valid)],
        data_structures=[_dsd(concept=dsd_valid)],
        dataflows=[_flow()],
    )
    return as_structure(parse(write30(document), strict=False))


def test_an_artefact_add_would_refuse_is_refused_and_the_rest_read():
    msg = _read(dsd_valid=False)

    assert [r.urn for r in msg.refused] == [str(_DSD_URN)]
    assert "ConceptIdentity is required" in msg.refused[0].reason
    assert "dimensions[FREQ].concept" in msg.refused[0].reason
    assert msg.data_structures.ids() == []
    assert msg.dataflows.ids() == ["DF"]
    assert msg.concept_schemes.ids() == ["CS"]


def test_a_valid_document_refuses_nothing():
    msg = _read()

    assert msg.refused == ()
    assert msg.data_structures.ids() == ["DSD"]


def test_a_reference_to_a_refused_artefact_is_withdrawn():
    valid = present(_read().dataflows["DF"].structure)
    refused = present(_read(dsd_valid=False).dataflows["DF"].structure)

    assert valid.resolved
    assert not refused.resolved
    assert refused.urn == _DSD_URN


def test_a_reference_into_a_refused_scheme_is_withdrawn():
    """A concept is an item: the ref names the scheme only through its URN."""
    valid = present(_read().data_structures["DSD"].dimensions["FREQ"].concept)
    msg = _read(scheme_valid=False)
    refused = present(msg.data_structures["DSD"].dimensions["FREQ"].concept)

    assert [r.artefact_class for r in msg.refused] == ["ConceptScheme"]
    assert valid.resolved
    assert not refused.resolved


def test_strict_raises_naming_the_refusal_and_carrying_the_rest():
    document = sl.StructureMessage(data_structures=[_dsd(concept=False)], dataflows=[_flow()])

    with pytest.raises(SdmxConformanceError, match="DSD") as info:
        parse(write30(document))

    assert [r.urn for r in info.value.refused] == [str(_DSD_URN)]
    assert info.value.message is not None
    assert info.value.message.dataflows.ids() == ["DF"]


def test_a_non_strict_read_interns_what_it_kept():
    """`add_all` would have refused the batch; the refused DSD is not in it."""
    registry = InMemoryRegistry()
    document = sl.StructureMessage(
        concept_schemes=[_concepts()], data_structures=[_dsd(concept=False)], dataflows=[_flow()]
    )

    parse(write30(document), strict=False, registry=registry)

    assert _DSD_URN not in registry
    assert sl.Dataflow.urn_for(agency="T", id="DF", version="1.0") in registry


# ── RestRegistry reads on ─────────────────────────────────────────────────────


def _codelist(id_: str, *, valid: bool) -> sl.Codelist:
    with unchecked():
        code = sl.Code(id="A" if valid else "A A", name={"en": "a"})
    return sl.Codelist(id=id_, maintainer="T", name={"en": id_}, codes=[code])


def _serving_codelists() -> httpx.MockTransport:
    body = write21(sl.StructureMessage(codelists=[_codelist("CL_OK", valid=True), _codelist("CL_BAD", valid=False)]))
    return httpx.MockTransport(lambda _request: httpx.Response(200, content=body))


def test_a_rest_fetch_keeps_what_it_can_and_says_what_it_refused():
    """The provider serves both codelists for any request, as a bulk query would."""
    with sl.RestRegistry("https://example.test/service", id="T", transport=_serving_codelists()) as reg:
        assert reg.get(sl.Codelist.urn_for(agency="T", id="CL_OK", version="1.0")) is not None
        assert [r.urn for r in reg.refused] == [str(sl.Codelist.urn_for(agency="T", id="CL_BAD", version="1.0"))]


def test_getting_a_refused_artefact_raises_with_its_reason():
    refusal = r"CL_BAD\(1\.0\) was served and refused: .*Code\.id — id 'A A'"
    with (
        sl.RestRegistry("https://example.test/service", id="T", transport=_serving_codelists()) as reg,
        pytest.raises(SdmxConformanceError, match=refusal),
    ):
        reg.get(sl.Codelist.urn_for(agency="T", id="CL_BAD"))


# ── what no per-artefact refusal catches ──────────────────────────────────────


def test_a_metadata_set_the_model_refuses_fails_the_parse_as_a_parse_error():
    """The model refuses an empty id as it is set (#492); `parse` still raises only its own error.

    The metadata readers do not refuse per set, so the refusal reaches
    `parse`, which converts it as it converts lxml's and msgspec's.
    """
    path = find_root() / "tests" / "fixtures" / "sdmx_metadata" / "sdmx_twg_constructed_sample.json"
    doc = msgspec.json.decode(path.read_bytes(), type=dict[str, Any])
    doc["data"]["metadataSets"][0]["id"] = ""
    with pytest.raises(SdmxParseError, match=r"holds a value no SDMX artefact can: .*MetadataSet\.id") as info:
        parse(msgspec.json.encode(doc))
    assert isinstance(info.value.__cause__, ValidationError)


# ── a value the model refuses, inside an artefact ─────────────────────────────


def _dsd_with_facet(max_length: int) -> sl.DataStructure:
    with unchecked():
        facet = sl.Facet(type=sl.DataType.String, max_length=max_length)
    return sl.DataStructure(
        id="DSD",
        maintainer="T",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=facet)],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


@pytest.mark.parametrize("write", [write21, write30], ids=["2.1", "3.0"])
def test_a_zero_facet_bound_refuses_its_artefact_and_the_rest_read(write):
    """`Facet` refuses `maxLength="0"` as it is built (#492); the reader refuses the DSD alone."""
    msg = as_structure(
        parse(write(sl.StructureMessage(data_structures=[_dsd_with_facet(0)], dataflows=[_flow()])), strict=False)
    )

    assert [r.urn for r in msg.refused] == [str(_DSD_URN)]
    assert "Facet.max_length — max_length must be a positive integer" in msg.refused[0].reason
    assert msg.dataflows.ids() == ["DF"]


def test_the_same_document_with_a_positive_bound_refuses_nothing():
    msg = as_structure(parse(write30(sl.StructureMessage(data_structures=[_dsd_with_facet(1)])), strict=False))

    assert msg.refused == ()
    assert msg.data_structures.ids() == ["DSD"]
