"""A 2.1 metadata target holds every target object it names (#422).

``MetadataTargetType`` is a choice of five target elements, ``maxOccurs``
unbounded, and the model kept the first. `MetadataTarget.target_objects` holds
them all, in order. The maximal fixture's first target has two, which
`tests/unit/test_field_coverage.py` proves survive a 2.1 round trip; this file
holds the wire shape and the refusal.
"""

from typing import cast

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import serialize
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.metadatastructure import MetadataTarget, TargetObject
from sdmxlib.model.validation import ValidationError
from tests.support import maximal

pytestmark = pytest.mark.unit

_STR = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"


def test_sdmx_ml_2_1_writes_every_target_object_in_order() -> None:
    doc = etree.fromstring(ml21_writer.write(maximal.message()))
    target = next(el for el in doc.iter(f"{_STR}MetadataTarget") if el.get("id") == "TARGET")
    assert [
        (etree.QName(el).localname, el.get("id")) for el in target if etree.QName(el).localname != "Annotations"
    ] == [
        ("IdentifiableObjectTarget", "COMP"),
        ("DataSetTarget", "DATA_SET_TARGET"),
    ]


def test_sdmx_ml_2_1_reads_every_target_object_in_order() -> None:
    (msd,) = ml21_reader.read(ml21_writer.write(maximal.message())).metadata_structures
    (original,) = maximal.message().metadata_structures
    # Field by field less the annotations, whose second URL 2.1 cannot carry
    # (declared in `gaps.py`, proved by `test_field_coverage`).
    assert [attrs.evolve(obj, annotations=Annotations()) for obj in msd.targets[0].target_objects] == [
        attrs.evolve(obj, annotations=Annotations()) for obj in original.targets[0].target_objects
    ]
    assert [obj.kind for obj in msd.targets[0].target_objects] == ["IdentifiableObjectTarget", "DataSetTarget"]


def test_a_target_object_is_not_a_bare_string() -> None:
    kinds: object = ["IdentifiableObjectTarget"]
    with pytest.raises(TypeError, match="iterable of TargetObject"):
        MetadataTarget(id="T", target_objects=cast("tuple[TargetObject, ...]", kinds))


def test_sdmx_ml_2_1_refuses_a_target_with_no_target_object() -> None:
    msg = maximal.message()
    (msd,) = msg.metadata_structures
    emptied = attrs.evolve(msd, targets=[attrs.evolve(msd.targets[0], target_objects=()), *msd.targets[1:]])
    with pytest.raises(ValidationError) as caught:
        serialize(attrs.evolve(msg, metadata_structures=[emptied]), version="2.1")
    paths = [issue.path for issue in caught.value.issues if issue.path.startswith("MetadataStructure:")]
    assert paths == ["MetadataStructure:MSD.targets[TARGET].target_objects"]


def test_the_maximal_msd_is_writable_in_2_1() -> None:
    """Discrimination for the refusals here: every kind's own text type is accepted — a report period's a time type.

    The maximal message writes whole since #421; it used to be refused for
    other artefacts, so this filtered for the MSD's refusals.
    """
    serialize(maximal.message(), version="2.1")


def test_a_target_object_s_text_type_is_checked_on_each() -> None:
    msg = maximal.message()
    (msd,) = msg.metadata_structures
    bad = TargetObject(id="DATA_SET_TARGET", kind="DataSetTarget", representation=None)
    second = attrs.evolve(
        bad, representation=attrs.evolve(msd.targets[0].target_objects[0].representation, type="String")
    )
    target = attrs.evolve(msd.targets[0], target_objects=(msd.targets[0].target_objects[0], second))
    changed = attrs.evolve(msd, targets=[target, *msd.targets[1:]])
    with pytest.raises(ValidationError) as caught:
        serialize(attrs.evolve(msg, metadata_structures=[changed]), version="2.1")
    paths = [issue.path for issue in caught.value.issues if issue.path.startswith("MetadataStructure:")]
    assert paths == ["MetadataStructure:MSD.targets[TARGET].target_objects[DATA_SET_TARGET].representation.type"]
