"""`AnnotationValue` survives every format that has it — #368.

SDMX 3.0 added a fifth child to `AnnotationType`: `AnnotationValue`, the
**non-localised** body, distinct from `AnnotationText`'s language map. It is the
right home for a machine-read payload — an ordinal, a flag, a numeric weight —
where a language map means nothing.

The model had no field for it, so it was read off the wire and dropped with no
error. That is the #355 class of silent loss, and it was invisible to the field
census for the most basic reason available: **the census diffs fields the model
has**, and this one did not exist. A field nobody modelled cannot be observed
missing.

2.1 genuinely cannot carry it — its `AnnotationType` has four children — so
that direction is a declared limit in `formats/gaps.py`, not a defect. This file
pins the asymmetry: three formats must keep it and one must declare dropping
it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from lxml import etree

from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import reader as r21
from sdmxlib.formats.sdmx_ml21 import writer as w21
from sdmxlib.formats.sdmx_ml30 import reader as r30
from sdmxlib.formats.sdmx_ml30 import writer as w30
from sdmxlib.formats.sdmx_ml31 import reader as r31
from sdmxlib.formats.sdmx_ml31 import writer as w31
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.message import StructureMessage
from tests.support.xsd import SchemaVersion, compile_structure_schema

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

#: Carries `text` *and* `value` together — they are siblings, not alternatives,
#: and a writer that treated one as the other would still pass a test that set
#: only one.
_ANNOTATION = Annotation(type="ORDER", text={"en": "second"}, value="239")


def _message() -> StructureMessage:
    return StructureMessage(
        codelists=[
            Codelist(
                id="CL",
                maintainer="T",
                version="1.0",
                name={"en": "c"},
                annotations=Annotations([_ANNOTATION]),
                codes=ItemList([Code(id="A", name={"en": "a"}, annotations=Annotations([_ANNOTATION]))]),
            )
        ]
    )


#: Callables rather than modules, so the parameters can be *typed* — a
#: `writer: object` would need a suppression at every `.write` call.
_ML = [
    pytest.param(w30.write, r30.read, "3.0", id="3.0"),
    pytest.param(w31.write, r31.read, "3.1", id="3.1"),
]


@pytest.mark.parametrize(("writer", "reader", "version"), _ML)
def test_sdmx_ml_3x_round_trips_the_value(
    writer: Callable[[StructureMessage], bytes], reader: Callable[[bytes], StructureMessage], version: SchemaVersion
) -> None:
    """Both the artefact's own annotation and an item's."""
    written = writer(_message())
    cl = reader(written).codelists["CL"]
    assert cl.annotations[0].value == "239"
    assert cl.codes["A"].annotations[0].value == "239"
    assert cl.codes["A"].annotations[0].text["en"].text == "second", "text and value are siblings, not alternatives"


@pytest.mark.parametrize(("writer", "reader", "version"), _ML)
def test_the_emitted_3x_document_is_xsd_valid(
    writer: Callable[[StructureMessage], bytes], reader: Callable[[bytes], StructureMessage], version: SchemaVersion
) -> None:
    """`AnnotationValue` is last in the XSD sequence, after `AnnotationText`.

    An element out of order is rejected outright, so placement is not a detail
    the round-trip above would catch — our own reader is order-insensitive.
    """
    written = writer(_message())
    schema = compile_structure_schema(version)
    assert schema.validate(etree.fromstring(written)), str(schema.error_log)


def test_sdmx_json_round_trips_the_value() -> None:
    raw = json_writer.write(_message())
    emitted = json.loads(raw)["data"]["codelists"][0]["annotations"][0]
    assert emitted["value"] == "239"
    assert emitted["text"] == "second", "value must not displace the localised text"
    cl = json_reader.read(raw).codelists["CL"]
    assert cl.annotations[0].value == "239"
    assert cl.codes["A"].annotations[0].value == "239"


def test_sdmx_ml_21_drops_it_and_stays_valid() -> None:
    """The declared limit. 2.1's `AnnotationType` has no such child.

    Emitting it anyway would produce a document the official XSD rejects, so
    the loss is real and belongs in `gaps.py` — which
    `test_field_coverage.py` checks it is in. Asserted here as *behaviour*
    rather than left to the register, because "we chose to drop this" and "we
    forgot to write this" look identical from the output alone.
    """
    written = w21.write(_message())
    assert b"AnnotationValue" not in written
    schema = compile_structure_schema("2.1")
    assert schema.validate(etree.fromstring(written)), str(schema.error_log)
    cl = r21.read(written).codelists["CL"]
    assert cl.annotations[0].value is None
    assert cl.annotations[0].text["en"].text == "second", "only `value` is lost — the rest survives"


def test_the_gap_is_declared_for_21_only() -> None:
    """And declared *nowhere else* — 3.0, 3.1 and JSON must carry it.

    A register entry with too wide a target would excuse a real regression in a
    format that can hold the field perfectly well.
    """
    import sdmxlib as sl

    codelist = _message().codelists["CL"]
    by_target = {t: [g.field for g in sl.unrepresentable(codelist, t)] for t in ("2.1", "3.0", "3.1", "json")}
    assert "annotations[].value" in by_target["2.1"]
    for target in ("3.0", "3.1", "json"):
        assert "annotations[].value" not in by_target[target], f"{target} can carry it; declaring it lost hides bugs"


def test_a_value_free_annotation_declares_no_gap() -> None:
    """`unrepresentable` answers for *this* artefact, not the table in general."""
    import sdmxlib as sl

    plain = Codelist(
        id="CL",
        maintainer="T",
        version="1.0",
        name={"en": "c"},
        annotations=Annotations([Annotation(type="ORDER", text={"en": "second"})]),
    )
    assert [g.field for g in sl.unrepresentable(plain, "2.1") if "annotations" in g.field] == []
