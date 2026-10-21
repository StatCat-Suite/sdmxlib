"""Foundation of the msgspec SDMX-JSON migration — shared wire Structs & conventions.

Locks the conventions in `formats/sdmx_json/_dto.py` that every artefact DTO
builds on: the istring split, annotation convert, and — critically — the
``omit_defaults`` required-field discipline that the codelist spike tripped on.
"""

import msgspec
import pytest

from sdmxlib.formats.sdmx_json import _dto
from sdmxlib.formats.sdmx_json._dto import (
    Annotation,
    Meta,
    Sender,
    emit_annotations,
    emit_istr,
    ingest_annotations,
    ingest_istr,
)
from sdmxlib.model.annotations import Annotation as ModelAnnotation
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.istring import InternationalString

pytestmark = pytest.mark.unit


# ── The omit_defaults required-field discipline (the spike's `version` bug) ──


class _Defaulted(msgspec.Struct, omit_defaults=True):
    id: str
    version: str = "1.0"  # WRONG for an always-present field


class _Required(msgspec.Struct, omit_defaults=True):
    id: str
    version: str  # RIGHT: required → always emitted


def test_omit_defaults_drops_field_equal_to_its_default() -> None:
    # This is the trap: a value that happens to equal the default disappears.
    out = msgspec.json.decode(msgspec.json.encode(_Defaulted(id="X", version="1.0")))
    assert "version" not in out


def test_required_field_is_always_emitted() -> None:
    # The discipline: always-present fields are declared required (no default).
    out = msgspec.json.decode(msgspec.json.encode(_Required(id="X", version="1.0")))
    assert out["version"] == "1.0"


# ── InternationalString ↔ (name, names) ─────────────────────────────────────


def test_emit_ingest_istr_roundtrip() -> None:
    v = InternationalString.of({"en": "Frequency", "fr": "Fréquence"})
    name, names = emit_istr(v)
    assert name == "Frequency"  # primary = first language
    assert names == {"en": "Frequency", "fr": "Fréquence"}
    assert ingest_istr(name, names) == {"en": "Frequency", "fr": "Fréquence"}


def test_emit_istr_empty_is_none_none() -> None:
    assert emit_istr(None) == (None, None)
    assert emit_istr(InternationalString()) == (None, None)


def test_ingest_istr_singular_only_uses_default_lang() -> None:
    # Producer sent `name` but no `names` map — key under the _default sentinel.
    assert ingest_istr("Freq", None) == {_dto._DEFAULT_LANG: "Freq"}
    assert ingest_istr(None, None) == {}


def test_emit_istr_shares_map_not_copy() -> None:
    # copy=False on the hot path — the returned map is the model's backing dict.
    v = InternationalString.of({"en": "x"})
    _, names = emit_istr(v)
    assert names is v.as_dict(copy=False)


# ── Shared Structs: omit_defaults + roundtrip ────────────────────────────────


def test_annotation_omit_defaults() -> None:
    out = msgspec.json.decode(msgspec.json.encode(Annotation(id="A1")))
    assert out == {"id": "A1"}  # every None field dropped


# ── gc=False invariant: wire DTOs are transient acyclic trees ────────────────


def test_wire_dtos_are_gc_untracked() -> None:
    """Every wire DTO inherits ``WireStruct`` (gc=False), so instances are not
    tracked by the cyclic collector — the transient-encode fast path. Sample one
    DTO from each of the four wire modules; a stray ``msgspec.Struct`` base (or a
    dropped ``gc=False``) would re-track it and silently undo the win.
    """
    import gc

    from sdmxlib.formats.sdmx_json._dto import Link, WireStruct
    from sdmxlib.formats.sdmx_json._structure_dto import CodeJson
    from sdmxlib.formats.sdmx_json.metadata import ReportedAttributeJson
    from sdmxlib.formats.sdmx_json_1_0.writer import KeyValueJson

    samples = [
        Annotation(id="A1"),
        Link(rel="self", href="https://x/y"),
        CodeJson(id="C"),
        ReportedAttributeJson(id="R"),
        KeyValueJson(id="D", values=["A"]),
    ]
    for s in samples:
        assert isinstance(s, WireStruct)
        assert not gc.is_tracked(s), f"{type(s).__name__} is gc-tracked — lost gc=False"


def test_meta_roundtrip() -> None:
    m = Meta(id="MSG", prepared="2026-01-01T00:00:00", sender=Sender(id="SDMX"))
    back = msgspec.json.decode(msgspec.json.encode(m), type=Meta)
    assert back == m


def test_annotation_unknown_fields_ignored_on_decode() -> None:
    # Forward-compat: a producer extension field decodes without error.
    raw = b'{"id": "A1", "someVendorField": 42}'
    a = msgspec.json.decode(raw, type=Annotation)
    assert a.id == "A1"


# ── Annotation ↔ model roundtrip ─────────────────────────────────────────────


def test_annotations_roundtrip_through_wire() -> None:
    model = Annotations(
        [
            ModelAnnotation(id="a1", title="T", type="NOTE", urls="https://x/y", text={"en": "hello"}),
            ModelAnnotation(id="a2"),
        ]
    )
    wire = emit_annotations(model)
    assert wire is not None
    # url → links[rel=self].href
    assert wire[0].links == [_dto.Link(rel="self", href="https://x/y")]
    assert wire[0].texts == {"en": "hello"}
    # roundtrip through JSON bytes and back into the model
    encoded = _dto.ENCODER.encode(wire)
    decoded = msgspec.json.decode(encoded, type=list[Annotation])
    back = ingest_annotations(decoded)
    assert [a.id for a in back] == ["a1", "a2"]
    assert [u.url for u in back[0].urls] == ["https://x/y"]
    assert back[0].text.as_dict() == {"en": "hello"}
    assert back[0].type == "NOTE"


def test_emit_annotations_empty_is_none() -> None:
    assert emit_annotations(None) is None
    assert emit_annotations(Annotations()) is None
    assert ingest_annotations(None) == Annotations()
