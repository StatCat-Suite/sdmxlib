"""A reader refuses the artefact that contradicts the schema, not the document (#435).

registry.sdmx.org's 3.0 ``datastructure`` document carries 23 DSDs, three of
which enumerate a dimension with a ConceptScheme. The reader raised on the
first of them and all 23 were lost. Each reader now records a `Refusal` for the
bad artefact and reads the rest; `parse()` raises over the refusals by default
and returns what was read with ``strict=False``.

Each format is exercised with a contradiction its own reader really raises:
a wrong-class enumeration URN (3.0), an out-of-vocabulary constraint type
(2.1), and a character XML cannot carry (SDMX-JSON).
"""

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml._v3_reader import V3Reader
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import SdmxConformanceError
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_U = "urn:sdmx:org.sdmx.infomodel"


# ── Documents: one good artefact and one bad, per format ──────────────────────


def _dsd30(id_: str, enumeration: str) -> str:
    return f"""<str:DataStructure agencyID="UNSD" id="{id_}" version="1.2">
      <com:Name xml:lang="en">{id_}</com:Name>
      <str:DataStructureComponents><str:DimensionList id="DimensionDescriptor">
        <str:Dimension id="MEASURE" position="1">
          <str:ConceptIdentity>{_U}.conceptscheme.Concept=UNSD:CS_IMTS(1.0).MEASURE</str:ConceptIdentity>
          <str:LocalRepresentation><str:Enumeration>{enumeration}</str:Enumeration></str:LocalRepresentation>
        </str:Dimension>
      </str:DimensionList></str:DataStructureComponents>
    </str:DataStructure>"""


def _ml30(*dsds: str) -> bytes:
    return f"""<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message"
      xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure"
      xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common">
      <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test>
        <mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id="X"/></mes:Header>
      <mes:Structures><str:DataStructures>{"".join(dsds)}</str:DataStructures></mes:Structures>
    </mes:Structure>""".encode()


#: The registry.sdmx.org shape: a good DSD, and one whose dimension is
#: enumerated by a ConceptScheme — which the 3.0 schema forbids for a Dimension.
_GOOD_30 = _dsd30("GOOD", f"{_U}.codelist.Codelist=SDMX:CL_FREQ(2.0)")
_BAD_30 = _dsd30("IMTS", f"{_U}.conceptscheme.ConceptScheme=UNSD:CS_MEASURE(1.0)")
_ML30 = _ml30(_GOOD_30, _BAD_30)


def _constraint21(id_: str, type_: str) -> str:
    return f"""<str:ContentConstraint id="{id_}" agencyID="TEST" version="1.0" type="{type_}">
      <com:Name xml:lang="en">{id_}</com:Name>
    </str:ContentConstraint>"""


def _ml21(*constraints: str) -> bytes:
    return f"""<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
  xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
  xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
  <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test>
    <mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id="X"/></mes:Header>
  <mes:Structures><str:Constraints>{"".join(constraints)}</str:Constraints></mes:Structures>
</mes:Structure>""".encode()


#: `type` outside {Allowed, Actual} is a conformance error in the 2.1 reader.
_GOOD_21 = _constraint21("CC_GOOD", "Allowed")
_ML21 = _ml21(_GOOD_21, _constraint21("CC_BAD", "Nonsense"))


def _codelist_json(id_: str, name: str) -> dict[str, object]:
    return {"id": id_, "agencyID": "TEST", "version": "1.0", "names": {"en": name}, "codes": []}


#: U+0000 is legal in a JSON string and has no SDMX-ML form, so the SDMX-JSON
#: reader refuses the codelist named with it.
def _json(*codelists: dict[str, object]) -> bytes:
    return json.dumps({"data": {"codelists": list(codelists)}}).encode()


_GOOD_JSON = _codelist_json("CL_GOOD", "good")
_JSON = _json(_GOOD_JSON, _codelist_json("CL_BAD", "bad\u0000name"))

#: (document, the refused artefact's class, its id, the surviving artefact's
#: slot and id, the refused one's URN).
_CASES = [
    pytest.param(
        _ML30,
        "DataStructure",
        "data_structures",
        "GOOD",
        f"{_U}.datastructure.DataStructure=UNSD:IMTS(1.2)",
        id="sdmx-ml-3.0",
    ),
    pytest.param(
        _ML21,
        "DataConstraint",
        "constraints",
        "CC_GOOD",
        f"{_U}.registry.DataConstraint=TEST:CC_BAD(1.0)",
        id="sdmx-ml-2.1",
    ),
    pytest.param(
        _JSON,
        "Codelist",
        "codelists",
        "CL_GOOD",
        f"{_U}.codelist.Codelist=TEST:CL_BAD(1.0)",
        id="sdmx-json",
    ),
]


def _ids(message: StructureMessage, slot: str) -> list[str]:
    artefacts: Iterable[MaintainableArtefact] = getattr(message, slot)
    return [a.id for a in artefacts]


# ── strict (the default): the call fails, and says what was refused ───────────


@pytest.mark.parametrize(("data", "cls", "slot", "good", "urn"), _CASES)
def test_parse_raises_naming_the_one_refused_artefact(data: bytes, cls: str, slot: str, good: str, urn: str) -> None:
    with pytest.raises(SdmxConformanceError) as caught:
        parse(data)
    err = caught.value
    assert len(err.refused) == 1
    (refusal,) = err.refused
    assert refusal.artefact_class == cls
    assert refusal.urn == urn
    assert refusal.reason
    assert urn in str(err)
    assert "1 artefact refused" in str(err)
    # Everything that *was* read rides on the error, so nothing has to be
    # parsed twice to recover it.
    assert err.message is not None
    assert _ids(err.message, slot) == [good]
    assert err.message.refused == err.refused


@pytest.mark.parametrize(("data", "cls", "slot", "good", "urn"), _CASES)
def test_parse_not_strict_returns_what_was_read(data: bytes, cls: str, slot: str, good: str, urn: str) -> None:
    msg = as_structure(parse(data, strict=False))
    assert _ids(msg, slot) == [good]
    assert [(r.artefact_class, r.urn) for r in msg.refused] == [(cls, urn)]
    # A refusal is not an artefact: it is in no slot, and not in all_artefacts.
    assert [a.id for a in msg.all_artefacts()] == [good]


@pytest.mark.parametrize(("data", "cls", "slot", "good", "urn"), _CASES)
def test_read_honours_strict(data: bytes, cls: str, slot: str, good: str, urn: str, tmp_path: Path) -> None:
    path = tmp_path / "doc"
    path.write_bytes(data)
    with pytest.raises(SdmxConformanceError):
        sl.read(path)
    msg = as_structure(sl.read(path, strict=False))
    assert _ids(msg, slot) == [good]
    assert [(r.artefact_class, r.urn) for r in msg.refused] == [(cls, urn)]


def test_the_refusal_names_the_reason_the_reader_gave() -> None:
    """The reason is the conformance error's own message, not a paraphrase."""
    msg = as_structure(parse(_ML30, strict=False))
    (refusal,) = msg.refused
    assert "requires a Codelist reference" in refusal.reason
    assert "ConceptScheme" in refusal.reason


def test_refusal_is_exported() -> None:
    assert sl.Refusal is type(as_structure(parse(_ML30, strict=False)).refused[0])


# ── The registry ──────────────────────────────────────────────────────────────


def _dsd_urn(id_: str) -> sl.SdmxUrn[sl.DataStructure]:
    return sl.DataStructure.urn_for(agency="UNSD", id=id_, version="1.2")


def test_not_strict_interns_only_what_was_read() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        parse(_ML30, registry=reg, strict=False)
        assert reg.get(_dsd_urn("GOOD")) is not None
        assert reg.get(_dsd_urn("IMTS")) is None


def test_strict_interns_nothing_when_it_raises() -> None:
    """A call that fails leaves no half of the document behind in the registry."""
    with sl.LocalRegistry(":memory:") as reg:
        with pytest.raises(SdmxConformanceError):
            parse(_ML30, registry=reg)
        assert reg.get(_dsd_urn("GOOD")) is None


# ── The catch is narrow ───────────────────────────────────────────────────────


def test_a_non_conformance_failure_still_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only `SdmxConformanceError` means "this artefact is wrong".

    Anything else raised while building an artefact is our bug; refusing the
    artefact over it would turn a defect into a silently shorter message.
    """

    def broken(*_args: object) -> object:
        msg = "a bug, not a bad document"
        raise RuntimeError(msg)

    monkeypatch.setattr(V3Reader, "_read_dsd_components", broken)
    with pytest.raises(RuntimeError, match="a bug, not a bad document"):
        parse(_ml30(_GOOD_30), strict=False)


def test_an_artefact_referencing_a_refused_one_is_not_refused() -> None:
    """A dataflow over the refused DSD is read; its structure ref stays unresolved."""
    flow = f"""<str:Dataflows><str:Dataflow agencyID="UNSD" id="DF_IMTS" version="1.0">
      <com:Name xml:lang="en">flow</com:Name>
      <str:Structure>{_U}.datastructure.DataStructure=UNSD:IMTS(1.2)</str:Structure>
    </str:Dataflow></str:Dataflows>"""
    data = _ML30.replace(b"</str:DataStructures>", f"</str:DataStructures>{flow}".encode())
    msg = as_structure(parse(data, strict=False))
    (dataflow,) = msg.dataflows
    assert dataflow.id == "DF_IMTS"
    assert dataflow.structure is not None
    assert dataflow.structure.resolved is False
    assert [r.urn for r in msg.refused] == [f"{_U}.datastructure.DataStructure=UNSD:IMTS(1.2)"]


# ── Regression: a document with nothing wrong in it ───────────────────────────


@pytest.mark.parametrize(
    ("data", "slot", "good"),
    [
        pytest.param(_ml30(_GOOD_30), "data_structures", "GOOD", id="sdmx-ml-3.0"),
        pytest.param(_ml21(_GOOD_21), "constraints", "CC_GOOD", id="sdmx-ml-2.1"),
        pytest.param(_json(_GOOD_JSON), "codelists", "CL_GOOD", id="sdmx-json"),
    ],
)
def test_a_clean_document_refuses_nothing(data: bytes, slot: str, good: str) -> None:
    msg = as_structure(parse(data))
    assert msg.refused == ()
    assert _ids(msg, slot) == [good]


def test_a_message_built_in_code_refuses_nothing() -> None:
    assert StructureMessage().refused == ()
