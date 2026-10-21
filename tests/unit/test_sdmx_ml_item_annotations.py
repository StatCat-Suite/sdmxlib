"""SDMX-ML writers must emit annotations on items/components, not just
maintainables. Sibling to the SDMX-JSON gap (#157).

Both XML readers parse item-level annotations (codes, concepts, categories
via the shared ``_common`` reader; dimensions / attributes / measures /
hierarchical codes / levels / metadata attributes via the per-format
readers), but the writers historically dropped them — silent round-trip
data loss. These tests inject annotations onto every component of a parsed
fixture, re-serialise (XSD-validated, which also catches the
``<com:Annotations>``-must-come-first ordering rule), and assert survival.
"""

from collections.abc import Iterable
from pathlib import Path

import attrs
import pytest

from sdmxlib.formats.sdmx_ml21 import reader as r21
from sdmxlib.formats.sdmx_ml21 import writer as w21
from sdmxlib.formats.sdmx_ml30 import reader as r30
from sdmxlib.formats.sdmx_ml30 import writer as w30
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.message import StructureMessage
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _ann(type_: str) -> Annotations:
    return Annotations([Annotation(id=f"ann_{type_}", type=type_, title=f"on-{type_}", urls=f"https://x/{type_}")])


def _annotated[T: attrs.AttrsInstance](items: Iterable[T], type_: str) -> list[T]:
    return [attrs.evolve(item, annotations=_ann(type_)) for item in items]


def _seeded(msg: StructureMessage) -> StructureMessage:
    """`msg` with one annotation on each item and component — a copy, the model being frozen (#492)."""

    def dsd_seeded(dsd: DataStructure) -> DataStructure:
        time = dsd.time_dimension
        return attrs.evolve(
            dsd,
            dimensions=_annotated(dsd.dimensions, "DIM"),
            time_dimension=None if time is None else attrs.evolve(time, annotations=_ann("TIME")),
            attributes=_annotated(dsd.attributes, "ATTR"),
            measures=_annotated(dsd.measures, "MEAS"),
        )

    return attrs.evolve(
        msg,
        codelists=[attrs.evolve(cl, codes=_annotated(cl.codes, "CODE")) for cl in msg.codelists],
        concept_schemes=[attrs.evolve(cs, concepts=_annotated(cs.concepts, "CONCEPT")) for cs in msg.concept_schemes],
        data_structures=[dsd_seeded(dsd) for dsd in msg.data_structures],
    )


@pytest.mark.parametrize(
    ("version", "reader", "writer"),
    [("2.1", r21, w21), ("3.0", r30, w30)],
)
def test_item_annotations_round_trip(version: str, reader, writer) -> None:
    ml_dir = "sdmx_ml21" if version == "2.1" else "sdmx_ml30"
    msg = reader.read((_FIXTURES / ml_dir / "minimal.xml").read_bytes())
    msg = _seeded(msg)

    written = writer.write(msg)
    # XSD validation catches the annotations-must-precede-Name ordering rule.
    assert_valid_sdmx_ml(written, structure_schema(version))
    back = reader.read(written)

    dsd = next(iter(back.data_structures))
    dim = dsd.dimensions[dsd.dimensions.ids()[0]]
    assert dim.annotations.first("DIM") is not None
    assert dim.annotations.first("DIM").title == "on-DIM"
    assert [u.url for u in dim.annotations.first("DIM").urls] == ["https://x/DIM"]

    if dsd.time_dimension is not None:
        assert dsd.time_dimension.annotations.first("TIME") is not None
    assert dsd.attributes[dsd.attributes.ids()[0]].annotations.first("ATTR") is not None
    assert dsd.measures[dsd.measures.ids()[0]].annotations.first("MEAS") is not None

    cl = next(iter(back.codelists))
    assert cl.codes[cl.codes.ids()[0]].annotations.first("CODE") is not None

    cs = next(iter(back.concept_schemes))
    assert cs.concepts[cs.concepts.ids()[0]].annotations.first("CONCEPT") is not None
