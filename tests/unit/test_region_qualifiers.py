"""A cube region's values and value sets carry their qualifiers (#422).

A value is a `RegionValue` — the code plus its cascade, language and validity —
and a value set carries 3.x's `removePrefix` and, for a dimension, a validity
period. The censuses prove every format and the store carry each (the maximal
fixture sets them all off their defaults); these pin the spellings, what each
SDMX version lets each position carry, and the refusals.
"""

import functools
import json
from collections.abc import Callable

import pytest
from attrs import evolve
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.codelist import CascadeSelection
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    CubeRegion,
    DataConstraint,
    KeyValue,
    RegionValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_V3: dict[SchemaVersion, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
}


@functools.cache
def _xsd(version: SchemaVersion) -> etree.XMLSchema:
    return compile_structure_schema(version)


def _region() -> CubeRegion:
    return CubeRegion(
        keys=(
            KeyValue(
                dimension_id="REF_AREA",
                values=[
                    RegionValue("EU", cascade=CascadeSelection.EXCLUDE_ROOT, valid_from="2020", valid_to="2024"),
                    "DE",
                ],
                remove_prefix=True,
                valid_from="2020-Q1",
            ),
        ),
        components=(
            ComponentValueSet(
                component_id="COMMENT",
                # Not the code alone, every format's default, which is left out (#503).
                values=[RegionValue("nota", lang="es", cascade=CascadeSelection.INCLUDE_DESCENDANTS)],
                remove_prefix=True,
            ),
        ),
    )


def _constraint(region: CubeRegion | None = None) -> DataConstraint:
    flow = Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0"))
    return DataConstraint(
        id="DC",
        maintainer="AG",
        version="1.0",
        name={"en": "DC"},
        attachment=ConstraintAttachment(dataflows=(flow,)),
        regions=[region or _region()],
    )


def test_a_bare_string_is_an_unqualified_value() -> None:
    key = KeyValue(dimension_id="FREQ", values=["A", RegionValue("Q", lang=None)])
    assert key.values == {RegionValue("A"), RegionValue("Q")}
    assert key.codes() == {"A", "Q"}


# ── SDMX-ML 3.x ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3_spells_each_qualifier_and_reads_it_back(version: SchemaVersion) -> None:
    write, read = _V3[version]
    xml = write(StructureMessage(constraints=[_constraint()]))
    assert_valid_sdmx_ml(xml, _xsd(version))
    doc = etree.fromstring(xml)
    key = next(el for el in doc.iter() if el.get("id") == "REF_AREA")
    assert (key.get("removePrefix"), key.get("validFrom"), key.get("validTo")) == ("true", "2020-Q1", None)
    eu, de = sorted(key.iter("{*}Value"), key=lambda v: v.text or "", reverse=True)[:2]
    assert (eu.text, eu.get("cascadeValues"), eu.get("validFrom"), eu.get("validTo")) == (
        "EU",
        "excluderoot",
        "2020",
        "2024",
    )
    assert de.attrib == {}, "an unqualified value is written bare"
    comment = next(el for el in doc.iter() if el.get("id") == "COMMENT")
    (nota,) = comment.iter("{*}Value")
    assert (nota.get(_XML_LANG), nota.get("cascadeValues")) == ("es", "true")
    assert read(xml).constraints[0].regions == (_region(),)


# ── SDMX-ML 2.1 ───────────────────────────────────────────────────────────


def test_sdmx_ml_2_1_refuses_excluderoot() -> None:
    """2.1's `cascadeValues` is a boolean; either one would change the codes named."""
    with pytest.raises(sl.ValidationError, match=r"values\[EU\]\.cascade"):
        sl.serialize(StructureMessage(constraints=[_constraint()]), version="2.1")


def test_sdmx_ml_2_1_writes_a_boolean_cascade_and_says_what_it_drops() -> None:
    region = CubeRegion(
        keys=(
            KeyValue(dimension_id="REF_AREA", values=[RegionValue("EU", cascade=CascadeSelection.INCLUDE_DESCENDANTS)]),
        )
    )
    xml = sl.serialize(StructureMessage(constraints=[_constraint(region)]), version="2.1")
    (value,) = (el for el in etree.fromstring(xml).iter() if el.text == "EU")
    assert value.get("cascadeValues") == "true"
    included = evolve(_region(), keys=(evolve(_region().keys[0], values=["EU"]),))
    assert sorted(gap.field for gap in sl.unrepresentable(_constraint(included), "2.1")) == [
        "regions[].components[].remove_prefix",
        "regions[].components[].values",
        "regions[].keys[].remove_prefix",
        "regions[].keys[].valid_from",
    ]


# ── SDMX-JSON ─────────────────────────────────────────────────────────────


def test_sdmx_json_writes_an_object_only_for_a_qualified_value() -> None:
    body = json_writer.write(StructureMessage(constraints=[_constraint()]))
    region = json.loads(body)["data"]["dataConstraints"][0]["cubeRegions"][0]
    key = region["keyValues"][0]
    assert key["values"] == [
        "DE",
        {"value": "EU", "cascadeValues": "excluderoot", "validFrom": "2020", "validTo": "2024"},
    ]
    assert (key["removePrefix"], key["validFrom"]) == (True, "2020-Q1")
    assert region["components"][0]["values"] == [{"value": "nota", "cascadeValues": True, "lang": "es"}]
    assert json_reader.read(body).constraints[0].regions == (_region(),)


def test_sdmx_json_reads_a_boolean_cascade() -> None:
    body = json.loads(json_writer.write(StructureMessage(constraints=[_constraint()])))
    body["data"]["dataConstraints"][0]["cubeRegions"][0]["keyValues"][0]["values"] = [
        {"value": "EU", "cascadeValues": True}
    ]
    (key,) = json_reader.read(json.dumps(body).encode()).constraints[0].regions[0].keys
    assert key.values == {RegionValue("EU", cascade=CascadeSelection.INCLUDE_DESCENDANTS)}


def test_sdmx_json_1_0_refuses_any_qualifier() -> None:
    with pytest.raises(sl.ValidationError) as caught:
        write_contentconstraint(_constraint())
    assert {issue.path.rsplit(".", 1)[1] for issue in caught.value.issues} == {"remove_prefix", "values"}


# ── what each position may carry ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("region", "path"),
    [
        pytest.param(
            CubeRegion(keys=(KeyValue(dimension_id="REF_AREA", values=[RegionValue("EU", lang="en")]),)),
            "keys[REF_AREA].values[EU].lang",
            id="a dimension's value has no language",
        ),
        pytest.param(
            CubeRegion(components=(ComponentValueSet(component_id="C", values=[RegionValue("x", valid_from="2020")]),)),
            "components[C].values[x].valid_from",
            id="a component's value has no validity",
        ),
        pytest.param(
            CubeRegion(keys=(KeyValue(dimension_id="REF_AREA", values=["EU", RegionValue("EU", valid_from="2020")]),)),
            "keys[REF_AREA].values",
            id="a value listed twice",
        ),
    ],
)
def test_what_sdmx_3_prohibits_is_refused(region: CubeRegion, path: str) -> None:
    with pytest.raises(sl.ValidationError, match=path.replace("[", r"\[").replace("]", r"\]")):
        _constraint(region)


def test_the_example_is_valid() -> None:
    """The discrimination check for the cases above: it builds."""
    _constraint()


# ── the store and the query ───────────────────────────────────────────────


def test_the_store_keeps_every_qualifier_and_a_query_reads_the_cascade() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_constraint())
        stored = reg.get(DataConstraint.urn_for(agency="AG", id="DC", version="1.0"))
        assert isinstance(stored, DataConstraint)
        assert stored.regions == (_region(),)
        cascading = reg.query(q.key_value).filter(q.key_value.cascade == "excluderoot")
        assert cascading.ids() == ["EU"]
