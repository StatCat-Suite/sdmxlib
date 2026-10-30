"""SDMX-JSON 2.0 annotation serialisation coverage. Closes #157.

The structure writer historically dropped ``annotations`` on every
artefact — a Dataflow loaded from a bundle carried its annotations in the
model, then the JSON writer threw them away. These tests pin the emitted
shape (``id`` / ``title`` / ``type`` scalars, ``text`` + ``texts`` for the
localised body, ``links`` with ``rel: "self"`` for the URL) and assert a
full write → read round-trip on a maintainable, an item, and a component.
"""

import msgspec
import pytest

from sdmxlib.formats.sdmx_json.reader import read as read_json
from sdmxlib.formats.sdmx_json.structure_values import serialize_code
from sdmxlib.formats.sdmx_json.writer import write as write_json
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref
from tests.support.narrow import present
from tests.support.roundtrip import roundtrip_json

pytestmark = pytest.mark.unit


# ── Issue #157 reproduction ───────────────────────────────────────────────────


def test_dataflow_default_annotation_reaches_json() -> None:
    """The exact bundle-baked DEFAULT annotation case from the issue."""
    df = Dataflow(
        id="B",
        maintainer="A",
        version="1.0",
        name={"en": "Flow"},
        annotations=Annotations([Annotation(id="default_lno", title="LASTNOBSERVATIONS=500", type="DEFAULT")]),
    )
    out = read_json(write_json(StructureMessage.containing(df)))
    rt_df = out.dataflows["B"]
    assert "DEFAULT" in rt_df.annotations
    ann = rt_df.annotations.first("DEFAULT")
    assert ann is not None
    assert ann.id == "default_lno"
    assert ann.title == "LASTNOBSERVATIONS=500"


# ── Emitted wire shape ────────────────────────────────────────────────────────


def test_annotation_url_emitted_as_self_link() -> None:
    """The annotation URL rides in ``links`` under ``rel: "self"`` per schema."""
    code = Code(
        name={"en": "A"},
        id="A",
        annotations=Annotations([Annotation(id="x", type="SOURCE", urls="https://example.org/a")]),
    )
    out = serialize_code(code)
    assert "annotations" in out
    assert out["annotations"] == [
        {"id": "x", "type": "SOURCE", "links": [{"rel": "self", "href": "https://example.org/a"}]}
    ]


def test_annotation_text_emitted_as_text_and_texts() -> None:
    code = Code(
        name={"en": "A"},
        id="A",
        annotations=Annotations([Annotation(title="t", text=InternationalString(en="hello", fr="bonjour"))]),
    )
    out = serialize_code(code)
    assert "annotations" in out
    (ann,) = out["annotations"]
    assert "texts" in ann
    assert ann["texts"] == {"en": "hello", "fr": "bonjour"}
    assert "text" in ann
    assert ann["text"] in ann["texts"].values()
    assert "id" not in ann  # None scalars are omitted


def test_no_annotations_key_when_empty() -> None:
    assert "annotations" not in serialize_code(Code(name={"en": "A"}, id="A"))


# ── Full-field round-trips ────────────────────────────────────────────────────


def test_maintainable_annotation_all_fields_round_trip() -> None:
    df = Dataflow(
        id="DF",
        maintainer="TEST",
        version="1.0",
        name={"en": "Flow"},
        annotations=Annotations(
            [
                Annotation(
                    id="a1",
                    title="A title",
                    type="NOTE",
                    urls="https://example.org/note",
                    text=InternationalString(en="English", fr="Français"),
                )
            ]
        ),
    )
    rt = roundtrip_json(StructureMessage.containing(df))
    (ann,) = rt.dataflows["DF"].annotations
    assert ann.id == "a1"
    assert ann.title == "A title"
    assert ann.type == "NOTE"
    assert [u.url for u in ann.urls] == ["https://example.org/note"]
    assert ann.text["en"].text == "English"
    assert ann.text["fr"].text == "Français"


def test_item_and_component_annotations_round_trip() -> None:
    """Annotations survive on a Code (item) and a Dimension (component)."""
    cl = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name={"en": "Freq"},
        codes=[
            Code(name={"en": "A"}, id="A", annotations=Annotations([Annotation(id="c", type="CTX", title="on-code")]))
        ],
    )
    dsd = DataStructure(
        id="DSD",
        maintainer="TEST",
        version="1.0",
        name={"en": "DSD"},
        dimensions=ItemList(
            [
                Dimension(
                    id="FREQ",
                    annotations=Annotations([Annotation(type="DIM", title="on-dim")]),
                    concept=concept_ref("FREQ"),
                )
            ]
        ),
    )
    rt = roundtrip_json(StructureMessage(codelists=[cl], data_structures=[dsd]))

    rt_code = rt.codelists["CL_FREQ"].codes["A"]
    assert rt_code.annotations.first("CTX") is not None
    assert present(rt_code.annotations.first("CTX")).title == "on-code"

    rt_dim = rt.data_structures["DSD"].dimensions["FREQ"]
    assert rt_dim.annotations.first("DIM") is not None
    assert present(rt_dim.annotations.first("DIM")).title == "on-dim"


def _bare_text(content_languages: list[str] | None) -> bytes:
    """A dataflow whose annotation has a scalar ``text`` and no ``texts`` map."""
    df = Dataflow(id="DF", maintainer="T", version="1.0", name={"en": "F"})
    doc = msgspec.json.decode(write_json(StructureMessage.containing(df)))
    doc["data"]["dataflows"][0]["annotations"] = [{"type": "X", "text": "plain"}]
    if content_languages is not None:
        doc["meta"]["contentLanguages"] = content_languages
    return msgspec.json.encode(doc)


def test_a_bare_text_takes_the_one_language_the_message_declares() -> None:
    """A scalar ``text`` states no language; ``contentLanguages`` naming one gives it (GH-280)."""
    rt = read_json(_bare_text(["en"]))
    ann = rt.dataflows["DF"].annotations.first("X")
    assert ann is not None
    assert ann.text == InternationalString(en="plain")


def test_a_bare_text_in_a_message_of_no_one_language_refuses_the_artefact() -> None:
    rt = read_json(_bare_text(None))
    assert len(rt.dataflows) == 0
    (refusal,) = rt.refused
    assert "contentLanguages" in refusal.reason


# Guard: SDMX-ML round-trip already handles annotations; here we only assert the
# JSON URL mapping is symmetric so the two formats agree on annotation identity.
def test_url_only_annotation_round_trip() -> None:
    urn: SdmxUrn[DataStructure] = SdmxUrn(
        package="datastructure", artefact_class="DataStructure", agency="T", id="DSD", version="1.0"
    )
    df = Dataflow(
        id="DF",
        maintainer="T",
        version="1.0",
        name={"en": "F"},
        structure=Ref[DataStructure].unresolved(urn),
        annotations=Annotations([Annotation(urls="https://example.org/only")]),
    )
    rt = roundtrip_json(StructureMessage.containing(df))
    (ann,) = rt.dataflows["DF"].annotations
    assert [u.url for u in ann.urls] == ["https://example.org/only"]
    assert ann.id is None
    assert ann.title is None
    assert ann.type is None
