"""What an SDMX-ML read fills in is recorded, and what it cannot fill in refuses (#474).

The schema requires some constructs that documents in the wild omit. For each
one a reader either assumes a value or refuses the artefact:

* assumed — the value is meaningful and a tolerance chose it (`ASSUMPTIONS`);
  the artefact is kept and the read says so on `StructureMessage.assumed`;
* refused — no value is worth assuming (a link with no url, an epoch map with
  no base period); the artefact is refused, the rest of the document read.

Each `ASSUMPTIONS` row is proved by removing its construct from the maximal
message's written form, once, and reading exactly one assumption back.
"""

from collections.abc import Callable, Iterable

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.sdmx_ml._allowed_children import NAMESPACES
from sdmxlib.formats.sdmx_ml._losses import ASSUMPTIONS, Assumption
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataAttribute
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError
from tests.support import maximal

pytestmark = pytest.mark.unit

_READERS = {"2.1": ml21_reader.read, "3.0": ml30_reader.read, "3.1": ml31_reader.read}
_WRITERS = {"2.1": ml21_writer.write, "3.0": ml30_writer.write, "3.1": ml31_writer.write}
_V3 = ("3.0", "3.1")


def _maximal(version: str) -> etree._Element:
    return etree.fromstring(_WRITERS[version](maximal.message()))


def _first(doc: etree._Element, xpath: str, version: str) -> etree._Element:
    found = doc.xpath(xpath, namespaces=NAMESPACES[version])
    assert isinstance(found, list)
    assert found, f"the maximal message has no {xpath} to remove from"
    el = found[0]
    assert isinstance(el, etree._Element)
    return el


def _urns(msg: StructureMessage) -> set[str]:
    return {str(sl.artefact_urn(a)) for a in msg.all_artefacts()}


# ── one removal per ASSUMPTIONS row ───────────────────────────────────────


def _drop_relationship(component: str) -> Callable[[etree._Element, str], None]:
    """Remove the first `component`'s AttributeRelationship."""

    def remove(doc: etree._Element, v: str) -> None:
        component_el = _first(doc, f"//str:AttributeList/str:{component}[str:AttributeRelationship]", v)
        relationship = _first(component_el, "str:AttributeRelationship", v)
        component_el.remove(relationship)

    return remove


def _drop_relationship_as_reporting_year_start_day(doc: etree._Element, v: str) -> None:
    """The maximal 2.1 message has no ReportingYearStartDay; make one from an attribute, relationship removed."""
    attribute = _first(doc, "//str:AttributeList/str:Attribute[str:AttributeRelationship]", v)
    attribute.tag = f"{{{NAMESPACES[v]['str']}}}ReportingYearStartDay"
    for child in attribute.xpath("str:AttributeRelationship | str:ConceptRole", namespaces=NAMESPACES[v]):
        assert isinstance(child, etree._Element)
        attribute.remove(child)


def _drop_attribute(xpath: str, name: str) -> Callable[[etree._Element, str], None]:
    def remove(doc: etree._Element, v: str) -> None:
        del _first(doc, f"{xpath}[@{name}]", v).attrib[name]

    return remove


def _recase_relationship(doc: etree._Element, v: str) -> None:
    """Spell one series attribute's first related dimension in lower case, as Eurostat's 3.0 DSDs do (#454)."""
    reference = _first(doc, "//str:AttributeList/str:Attribute/str:AttributeRelationship/str:Dimension", v)
    ref = reference.find("Ref")
    if ref is not None:
        ref.set("id", ref.get("id", "").lower())
    else:
        reference.text = (reference.text or "").lower()


def _drop_time_format(doc: etree._Element, v: str) -> None:
    """Remove the time dimension's LocalRepresentation, which every version requires (#507)."""
    time_dimension = _first(doc, "//str:DimensionList/str:TimeDimension[str:LocalRepresentation]", v)
    time_dimension.remove(_first(time_dimension, "str:LocalRepresentation", v))


_REMOVE: dict[tuple[str, str], Callable[[etree._Element, str], None]] = {
    **{(v, "str:AttributeRelationship/str:Dimension"): _recase_relationship for v in ("2.1", "3.0", "3.1")},
    ("2.1", "str:Attribute/str:AttributeRelationship"): _drop_relationship("Attribute"),
    ("2.1", "str:ReportingYearStartDay/str:AttributeRelationship"): _drop_relationship_as_reporting_year_start_day,
    **{(v, "str:Attribute/str:AttributeRelationship"): _drop_relationship("Attribute") for v in _V3},
    **{
        (v, "str:MetadataAttributeUsage/str:AttributeRelationship"): _drop_relationship("MetadataAttributeUsage")
        for v in _V3
    },
    **{(v, "@version"): _drop_attribute("//str:Codelist", "version") for v in _V3},
    ("3.0", "str:DataConstraint/@role"): _drop_attribute("//str:DataConstraint", "role"),
    **{(v, "str:EpochMap/@epochPeriod"): _drop_attribute("//str:EpochMap", "epochPeriod") for v in _V3},
    **{(v, "str:Hierarchy/@hasFormalLevels"): _drop_attribute("//str:Hierarchy", "hasFormalLevels") for v in _V3},
    **{(v, "str:TimeDimension/str:LocalRepresentation"): _drop_time_format for v in ("2.1", "3.0", "3.1")},
}

_CASES = [
    pytest.param(assumption, version, id=f"{version}:{assumption.construct}")
    for assumption in ASSUMPTIONS
    for version in assumption.versions
]


def test_every_row_has_a_removal() -> None:
    assert sorted(_REMOVE) == sorted((v, a.construct) for a in ASSUMPTIONS for v in a.versions)


def _removed(assumption: Assumption, version: str) -> bytes:
    doc = _maximal(version)
    _REMOVE[version, assumption.construct](doc, version)
    return etree.tostring(doc)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_the_maximal_message_reads_back_with_nothing_assumed(version: str) -> None:
    """The discrimination check: every assumption below is caused by its removal."""
    assert _READERS[version](_WRITERS[version](maximal.message())).assumed == ()


@pytest.mark.parametrize(("assumption", "version"), _CASES)
def test_each_assumption_is_reported_once_and_the_artefact_kept(assumption: Assumption, version: str) -> None:
    back = _READERS[version](_removed(assumption, version))
    (found,) = back.assumed
    assert (found.construct, found.assumed, found.count, found.reason) == (
        assumption.construct,
        assumption.assumed,
        1,
        assumption.reason,
    )
    assert found.urn in _urns(back), "the artefact is kept"
    assert back.refused == ()
    assert back.lost == ()


def test_an_assumption_counts_every_occurrence_in_its_artefact() -> None:
    doc = _maximal("3.0")
    for epoch_map in doc.xpath("//str:EpochMap", namespaces=NAMESPACES["3.0"]):
        assert isinstance(epoch_map, etree._Element)
        del epoch_map.attrib["epochPeriod"]
    back = _READERS["3.0"](etree.tostring(doc))
    assert sum(a.count for a in back.assumed) == 2
    assert {a.construct for a in back.assumed} == {"str:EpochMap/@epochPeriod"}


# ── parse: recorded by default, raised on request ─────────────────────────


def _with_an_assumption() -> bytes:
    (assumption,) = (a for a in ASSUMPTIONS if a.construct == "str:Hierarchy/@hasFormalLevels")
    return _removed(assumption, "3.0")


def test_parse_keeps_the_artefact_and_records_the_assumption() -> None:
    msg = sl.parse(_with_an_assumption())
    assert isinstance(msg, StructureMessage)
    assert [a.construct for a in msg.assumed] == ["str:Hierarchy/@hasFormalLevels"]
    assert str(msg.assumed[0]).endswith("x1 missing, assumed false — " + msg.assumed[0].reason)


def test_lossless_parse_raises_on_an_assumption() -> None:
    with pytest.raises(sl.LossyReadError, match=r"^1 assumption reading") as caught:
        sl.parse(_with_an_assumption(), lossless=True)
    assert [a.construct for a in caught.value.assumed] == ["str:Hierarchy/@hasFormalLevels"]
    assert caught.value.lost == ()
    read = caught.value.message
    assert isinstance(read, sl.StructureMessage)
    assert read.hierarchies, "what was read comes with the error"


# ── no value worth assuming: the artefact is refused ──────────────────────

#: (versions, element, attribute): every required attribute
#: whose absence refuses its artefact rather than building it with "".
_REFUSALS = [
    (_V3, "//com:Link", "rel"),
    (_V3, "//com:Link", "url"),
    (_V3, "//str:SentinelValue", "value"),
    (_V3, "//str:GeoFeatureSetCode", "value"),
    (_V3, "//str:EpochMap", "basePeriod"),
    (_V3, "//str:DatePatternMap", "sourcePattern"),
    (_V3, "//str:DatePatternMap", "locale"),
    (("2.1",), "//str:ReferencePeriod", "startTime"),
    (("2.1",), "//str:ReferencePeriod", "endTime"),
    (("2.1",), "//str:ValueMapping", "source"),
    (("2.1",), "//str:ValueMapping", "target"),
    (("2.1", *_V3), "//str:Codelist", "agencyID"),
]


def _owner(el: etree._Element) -> etree._Element:
    """The artefact element `el` sits in: the ancestor-or-self whose parent's parent is `mes:Structures`."""
    for node in (el, *el.iterancestors()):
        container = node.getparent()
        structures = None if container is None else container.getparent()
        if structures is not None and etree.QName(structures).localname == "Structures":
            return node
    msg = f"{etree.QName(el).localname} sits in no artefact"
    raise AssertionError(msg)


@pytest.mark.parametrize(
    ("version", "xpath", "attribute"),
    [pytest.param(v, x, a, id=f"{v}:{x[2:]}/@{a}") for vs, x, a in _REFUSALS for v in vs],
)
def test_a_missing_required_attribute_refuses_only_its_artefact(version: str, xpath: str, attribute: str) -> None:
    doc = _maximal(version)
    el = _first(doc, f"{xpath}[@{attribute}]", version)
    owner = _owner(el)
    del el.attrib[attribute]

    back = _READERS[version](etree.tostring(doc))

    (refusal,) = back.refused
    assert f":{owner.get('id')}(" in refusal.urn
    assert f"@{attribute}" in refusal.reason
    # A 2.1 StructureSet builds several artefacts, so the rest is checked by a
    # class the refused element is not.
    other = back.data_structures if etree.QName(owner).localname.endswith("Codelist") else back.codelists
    whole = _READERS[version](etree.tostring(_maximal(version)))
    expected = whole.data_structures if other is back.data_structures else whole.codelists
    assert other.ids() == expected.ids(), "the rest of the document is read"
    assert back.assumed == ()


def test_by_default_a_missing_required_attribute_fails_the_parse() -> None:
    doc = _maximal("3.0")
    del _first(doc, "//str:EpochMap[@basePeriod]", "3.0").attrib["basePeriod"]
    with pytest.raises(SdmxConformanceError, match="@basePeriod"):
        sl.parse(etree.tostring(doc))


# ── every required @id, found by walking the document ─────────────────────

#: Elements whose ``@id`` the schema makes optional: removing it is no defect.
_OPTIONAL_ID = frozenset({"Annotation", "Contact", "Sender"})

#: Elements whose ``@id`` the schema fixes: absent, it reads as the fixed
#: value, and the artefact is exactly as if the document had stated it.
_FIXED_ID = frozenset(
    {
        "AttributeList",  # AttributeDescriptor
        "DimensionList",  # DimensionDescriptor
        "MeasureList",  # MeasureDescriptor
        "MetadataAttributeList",  # MetadataAttributeDescriptor
        "TimeDimension",  # TIME_PERIOD
        "PrimaryMeasure",  # 2.1's OBS_VALUE, and its local reference's
    }
)

#: A component's ``@id`` is optional too, and defaults to its concept's (#477):
#: removed, the component is read under that id, which the maximal message's
#: components never share, so the read renames it.
_COMPONENT = frozenset(
    {
        "DataStructureComponents/DimensionList/Dimension",
        "DataStructureComponents/DimensionList/MeasureDimension",
        "DataStructureComponents/AttributeList/Attribute",
        "DataStructureComponents/MeasureList/Measure",
        "MetadataStructureComponents/ReportStructure/MetadataAttribute",
        "ReportStructure/MetadataAttribute/MetadataAttribute",
        "MetadataStructureComponents/MetadataAttributeList/MetadataAttribute",
        "MetadataAttributeList/MetadataAttribute/MetadataAttribute",
    }
)


def _concept_id(el: etree._Element) -> str:
    """The item id `el`'s ConceptIdentity names: a 2.1 ``<Ref>``'s ``@id``, or a 3.x URN's last part."""
    identity = el.find("{*}ConceptIdentity")
    assert identity is not None
    ref = identity.find("Ref")
    if ref is not None:
        return ref.get("id", "")
    item = SdmxUrn.parse((identity.text or "").strip()).item_id
    assert item is not None
    return item


#: Renamed ``PARENT``, a 3.x measure leaves the maximal DSD's attribute naming
#: ``OBS_VALUE``, a measure it no longer has: validation refuses that, rightly.
_RENAMED_INTO_A_REFUSAL: dict[tuple[str, str], str] = {
    **{(v, "DataStructureComponents/MeasureList/Measure"): "related_measures" for v in ("3.0", "3.1")},
    # Renamed, the dimension leaves the DSD's series attribute naming one it
    # no longer has, which validation refuses too (#454).
    **{(v, "DataStructureComponents/DimensionList/Dimension"): "related_dimensions" for v in ("2.1", "3.0", "3.1")},
}


def _component_ids(msg: StructureMessage) -> set[str]:
    ids = {
        c.id
        for dsd in msg.data_structures
        for c in (*dsd.dimensions, *dsd.attributes, *dsd.measures, *filter(None, [dsd.measure_dimension]))
    }

    def walk(attributes: Iterable[MetadataAttribute]) -> None:
        for attribute in attributes:
            ids.add(attribute.id)
            walk(attribute.children)

    for msd in msg.metadata_structures:
        walk(msd.attributes)
    return ids


def _path(el: etree._Element) -> str:
    """`el` and its two nearest ancestors, by local name: one case per shape, not per element."""
    return "/".join(etree.QName(node).localname for node in reversed([el, *el.iterancestors()][:3]))


def _id_bearing(version: str) -> list[str]:
    paths: dict[str, None] = {}
    for el in _maximal(version).iter():
        if isinstance(el.tag, str) and el.get("id") is not None and etree.QName(el).localname not in _OPTIONAL_ID:
            paths.setdefault(_path(el))
    return list(paths)


_ID_CASES = [pytest.param(v, p, id=f"{v}:{p}") for v in ("2.1", "3.0", "3.1") for p in _id_bearing(v)]


def test_the_walk_finds_every_shape() -> None:
    """The discrimination check: the walk reaches items, sub-structures, references and maintainables."""
    assert len(_ID_CASES) == 225  # 2.1: 105, 3.0: 60, 3.1: 60; each with the header's receiver (#480)
    paths = {p for _, p in (case.values for case in _ID_CASES)}
    for shape in (
        "Codelists/Codelist/Code",
        "Hierarchy/Level/Level",
        "Dataflow/Structure/Ref",
        "Structures/Codelists/Codelist",
    ):
        assert shape in paths


@pytest.mark.parametrize(("version", "path"), _ID_CASES)
def test_a_missing_id_reads_as_the_schema_says(version: str, path: str) -> None:
    doc = _maximal(version)
    (el,) = [el for el in doc.iter() if isinstance(el.tag, str) and el.get("id") is not None and _path(el) == path][:1]
    stated = el.attrib.pop("id")

    back = sl.parse(etree.tostring(doc), strict=False)

    assert isinstance(back, StructureMessage)
    if path.rsplit("/", maxsplit=1)[-1] in _FIXED_ID or path.endswith("PrimaryMeasure/Ref"):
        whole = sl.parse(etree.tostring(_maximal(version)), strict=False)
        assert isinstance(whole, StructureMessage)
        assert list(back.all_artefacts()) == list(whole.all_artefacts()), "read as the schema's fixed id"
        assert back.refused == ()
    elif (version, path) in _RENAMED_INTO_A_REFUSAL:
        (refusal,) = back.refused
        assert _RENAMED_INTO_A_REFUSAL[version, path] in refusal.reason
    elif path.startswith("Structure/Header/"):
        # A header party with no id: no header, and a loss, never a refusal (#480).
        assert back.refused == ()
        assert back.header is None
        assert [gap.construct for gap in back.lost if gap.construct == "mes:Header"] == ["mes:Header"]
    elif path in _COMPONENT:
        assert back.refused == (), f"{path} without @id is refused, not read as its concept's"
        assert _concept_id(el) != stated, "the derived id must differ to be seen"
        assert _concept_id(el) in _component_ids(back)
    else:
        assert len(back.refused) == 1, f"{path} without @id is not refused: {back.refused}"


@pytest.mark.parametrize("attribute", ["agencyID", "id"])
def test_a_2_1_metadata_provision_agreement_is_refused_once(attribute: str) -> None:
    """2.1 writes a metadata provision agreement as a ProvisionAgreement; refused, it is refused as one artefact.

    The account knew the element was built as another class only when it was
    built, so a refused one was refused again as a reader defect.
    """
    doc = _maximal("2.1")
    over_metadata = "//str:ProvisionAgreement[str:StructureUsage/Ref/@class='Metadataflow']"
    del _first(doc, over_metadata, "2.1").attrib[attribute]

    back = _READERS["2.1"](etree.tostring(doc))

    (refusal,) = back.refused
    assert refusal.artefact_class == "MetadataProvisionAgreement"


def test_a_component_with_neither_id_nor_concept_refuses_its_artefact() -> None:
    """Nothing to take the id from: the DSD is refused, as any other missing required id (#477)."""
    doc = _maximal("3.0")
    dimension = _first(doc, "//str:DimensionList/str:Dimension[@id]", "3.0")
    del dimension.attrib["id"]
    dimension.remove(_first(dimension, "str:ConceptIdentity", "3.0"))

    back = sl.parse(etree.tostring(doc), strict=False)

    assert isinstance(back, StructureMessage)
    (refusal,) = back.refused
    assert refusal.artefact_class == "DataStructure"
    assert "ConceptIdentity" in refusal.reason
