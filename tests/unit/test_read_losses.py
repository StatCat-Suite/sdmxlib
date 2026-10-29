"""What an SDMX-ML read cannot hold is recorded, never dropped silently (#422).

Two halves, as in `formats/sdmx_ml/_losses.py`:

* content inside an artefact the model has no field for — recorded per
  construct on `StructureMessage.lost`, the artefact kept;
* an artefact the reader built nothing from — refused, whether its class is
  unmodelled or the reader simply skipped it.

Each `LOSSES` row is proved twice. Injected into the maximal message's written
form, it must be reported exactly once. Written back, its own XPath must find it
again — which it cannot until the model holds it, so that case is a strict
xfail: model the construct and it XPASSes, and the row has to go.
"""

import json
from collections.abc import Callable

import msgspec
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_json._losses import _singular
from sdmxlib.formats.sdmx_json._structure_dto import StructureDataJson
from sdmxlib.formats.sdmx_json.reader import SECTIONS
from sdmxlib.formats.sdmx_ml import _common
from sdmxlib.formats.sdmx_ml._allowed_children import ALLOWED, NAMESPACES
from sdmxlib.formats.sdmx_ml._losses import LOSSES, Loss
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model._items import _maintainable_classes
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import SdmxConformanceError
from tests.support import maximal
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_READERS = {"2.1": ml21_reader.read, "3.0": ml30_reader.read, "3.1": ml31_reader.read}
_WRITERS = {"2.1": ml21_writer.write, "3.0": ml30_writer.write, "3.1": ml31_writer.write}
_V3 = ("3.0", "3.1")
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _maximal(version: str) -> etree._Element:
    return etree.fromstring(_WRITERS[version](maximal.message()))


def _first(doc: etree._Element, xpath: str, version: str) -> etree._Element:
    found = doc.xpath(xpath, namespaces=NAMESPACES[version])
    assert isinstance(found, list)
    assert found, f"the maximal message has no {xpath} to inject into"
    el = found[0]
    assert isinstance(el, etree._Element)
    return el


def _add(parent: etree._Element, qname: str, version: str, /, **attributes: str) -> etree._Element:
    prefix, local = qname.split(":")
    return etree.SubElement(parent, f"{{{NAMESPACES[version][prefix]}}}{local}", attrib=attributes)


# ── one injection per LOSSES row: the artefact element it lands in ───────


def _child(parent_xpath: str, qname: str, /, **attributes: str) -> Callable[[etree._Element, str], None]:
    """An injection appending one bare `qname` to the first `parent_xpath` — the element is the loss."""

    def inject(doc: etree._Element, v: str) -> None:
        _add(_first(doc, parent_xpath, v), qname, v, **attributes)

    return inject


def _attribute(xpath: str, name: str, value: str) -> Callable[[etree._Element, str], None]:
    """An injection setting one attribute on the first `xpath` — the attribute is the loss."""

    def inject(doc: etree._Element, v: str) -> None:
        _first(doc, xpath, v).set(name, value)

    return inject


def _dangling_level(doc: etree._Element, v: str) -> None:
    """Point the first node's level at one its hierarchy does not define: `<Ref id>` in 2.1, text in 3.x."""
    level = _first(doc, "//str:HierarchicalCode/str:Level", v)
    if (ref := level.find("Ref")) is not None:
        ref.set("id", "NOPE")
    else:
        level.text = "NOPE"


_INJECT: dict[str, Callable[[etree._Element, str], None]] = {
    "str:StructureSet/str:HybridCodelistMap": _child("//str:StructureSet", "str:HybridCodelistMap"),
    "str:StructureSet/str:ReportingTaxonomyMap": _child("//str:StructureSet", "str:ReportingTaxonomyMap"),
    "str:HierarchicalCode/str:Level": _dangling_level,
}

_CASES = [pytest.param(loss, version, id=f"{version}:{loss.construct}") for loss in LOSSES for version in loss.versions]


def test_every_row_has_an_injection() -> None:
    assert sorted(_INJECT) == sorted(loss.construct for loss in LOSSES)


def _injected(loss: Loss, version: str) -> bytes:
    doc = _maximal(version)
    _INJECT[loss.construct](doc, version)
    return etree.tostring(doc)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_the_maximal_message_reads_back_with_nothing_lost(version: str) -> None:
    """The discrimination check: every row's report below is caused by its injection."""
    back = _READERS[version](_WRITERS[version](maximal.message()))
    assert back.lost == ()
    assert back.refused == ()


@pytest.mark.parametrize(("loss", "version"), _CASES)
def test_each_loss_is_reported_once_and_the_artefact_kept(loss: Loss, version: str) -> None:
    back = _READERS[version](_injected(loss, version))
    (gap,) = back.lost
    assert (gap.construct, gap.count, gap.reason) == (loss.construct, 1, loss.reason)
    assert gap.urn in {str(sl.artefact_urn(a)) for a in back.all_artefacts()}, "the artefact is kept"
    assert back.refused == ()


@pytest.mark.parametrize(("loss", "version"), _CASES)
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="a declared loss (#448): modelled, delete its LOSSES row. A dangling level (#501) is never modelled",
)
def test_each_lost_construct_survives_a_round_trip(loss: Loss, version: str) -> None:
    """The burn-down: the row's own XPath must find it again after a write."""
    count = etree.XPath(loss.xpath, namespaces=NAMESPACES[version])
    original = etree.fromstring(_injected(loss, version))
    written = etree.fromstring(_WRITERS[version](_READERS[version](etree.tostring(original))))
    assert count(written) == count(original)


# ── parse: recorded by default, raised on request ─────────────────────────


_A_LOSS = "str:StructureSet/str:HybridCodelistMap"


def _with_a_loss() -> bytes:
    loss = next(loss for loss in LOSSES if loss.construct == _A_LOSS)
    return _injected(loss, "2.1")


def test_parse_keeps_the_artefact_and_records_the_loss() -> None:
    msg = sl.parse(_with_a_loss())
    assert isinstance(msg, StructureMessage)
    assert [gap.construct for gap in msg.lost] == [_A_LOSS]


def test_lossless_parse_raises_naming_the_loss_with_what_was_read() -> None:
    reg = sl.InMemoryRegistry()
    with pytest.raises(sl.LossyReadError, match="1 loss reading") as caught:
        sl.parse(_with_a_loss(), lossless=True, registry=reg)
    assert [gap.construct for gap in caught.value.lost] == [_A_LOSS]
    read = caught.value.message
    assert isinstance(read, sl.StructureMessage)
    assert read.codelists
    assert isinstance(caught.value, sl.SdmxParseError), "one except covers every failed read"
    assert reg.get(sl.artefact_urn(read.codelists[0])) is None, "nothing interned"


# ── artefacts built from nothing ──────────────────────────────────────────

#: Per version: every artefact class the schema allows in a document and the
#: model has no class for. Exact, so modelling one fails here until it leaves.
_UNMODELLED = {
    "2.1": [
        "CustomTypeScheme",
        "NamePersonalisationScheme",
        "Process",
        "ReportingTaxonomy",
        "RulesetScheme",
        "TransformationScheme",
        "UserDefinedOperatorScheme",
        "VtlMappingScheme",
    ],
    "3.0": [
        "CustomTypeScheme",
        "MetadataConstraint",
        "NamePersonalisationScheme",
        "Process",
        "ReportingTaxonomy",
        "ReportingTaxonomyMap",
        "RulesetScheme",
        "TransformationScheme",
        "UserDefinedOperatorScheme",
        "VtlMappingScheme",
    ],
}
#: 3.1's AvailabilityConstraint was here until #421 modelled it, and
#: DataConsumerScheme and OrganisationUnitScheme until #469.
_UNMODELLED["3.1"] = _UNMODELLED["3.0"]


def _containers(version: str) -> dict[str, str]:
    """Each artefact class the schema allows under `mes:Structures` → its container."""
    return {
        child.split(":")[1]: container
        for container in ALLOWED[version]["mes:Structures"]
        for child in ALLOWED[version].get(container, ())
        if not child.endswith(":Annotations")
    }


#: Modelled without being maintainable: 3.1's availability has no identity (#421).
_MODELLED_VALUES = frozenset({"AvailabilityConstraint"})


def _modelled(name: str) -> bool:
    modelled = {cls.__name__ for cls in _maintainable_classes()} | _MODELLED_VALUES
    return {"ContentConstraint": "DataConstraint"}.get(name, name) in modelled


def _document_with(version: str, name: str) -> bytes:
    """A small document plus one `name` element in its container."""
    doc = etree.fromstring(_WRITERS[version](StructureMessage(codelists=[_codelist()])))
    structures = _first(doc, "//mes:Structures", version)
    container = _add(structures, _containers(version)[name], version)
    _add(container, f"str:{name}", version, agencyID="AG", id="X", version="1.0")
    return etree.tostring(doc)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_the_unmodelled_classes_are_exactly_these(version: str) -> None:
    assert sorted(name for name in _containers(version) if not _modelled(name)) == _UNMODELLED[version]


@pytest.mark.parametrize(("version", "name"), [(v, n) for v, names in _UNMODELLED.items() for n in names])
def test_an_unmodelled_artefact_is_refused_not_skipped(version: str, name: str) -> None:
    back = _READERS[version](_document_with(version, name))
    (refusal,) = back.refused
    assert refusal.artefact_class == name
    assert refusal.reason == f"sdmxlib does not model {name}"
    assert "=AG:X(1.0)" in refusal.urn
    assert back.codelists.ids() == ["CL"], "the rest of the document is read"


def test_by_default_an_unmodelled_artefact_fails_the_parse() -> None:
    with pytest.raises(SdmxConformanceError, match="sdmxlib does not model Process"):
        sl.parse(_document_with("3.0", "Process"))


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_an_artefact_the_reader_skips_is_refused_as_a_defect(version: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The account is of the document, so a reader that drops a modelled class is caught too."""
    monkeypatch.setattr(_common.ReaderHelpers, "read_codelists", lambda *_args, **_kwargs: [])
    back = _READERS[version](_WRITERS[version](StructureMessage(codelists=[_codelist()])))
    (refusal,) = back.refused
    assert refusal.artefact_class == "Codelist"
    assert "reader defect" in refusal.reason


def _codelist() -> Codelist:
    return Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "CL"}, codes=[Code(id="A", name={"en": "a"})])


# ── SDMX-JSON ─────────────────────────────────────────────────────────────


def _maximal_json() -> dict[str, object]:
    body = json.loads(json_writer.write(maximal.message()))
    assert isinstance(body, dict)
    return body


def _section(body: dict[str, object], key: str) -> list[dict[str, object]]:
    data = body["data"]
    assert isinstance(data, dict)
    section = data[key]
    assert isinstance(section, list)
    return section


def _obj(value: object, key: str) -> dict[str, object]:
    assert isinstance(value, dict)
    found = value[key]
    assert isinstance(found, dict)
    return found


def _list(value: object, key: str) -> list[object]:
    assert isinstance(value, dict)
    found = value[key]
    assert isinstance(found, list)
    return found


def _key_value(body: dict[str, object]) -> dict[str, object]:
    region = _list(_section(body, "dataConstraints")[0], "cubeRegions")[0]
    key_value = _list(region, "keyValues")[0]
    assert isinstance(key_value, dict)
    return key_value


def _json_object_value(body: dict[str, object], **qualifiers: object) -> None:
    values = _list(_key_value(body), "values")
    first = values[0]
    code = first["value"] if isinstance(first, dict) else first
    values[0] = {"value": code, **qualifiers}


def test_the_maximal_message_reads_back_from_sdmx_json_with_nothing_lost() -> None:
    back = json_reader.read(json_writer.write(maximal.message()))
    assert back.lost == ()
    assert back.refused == ()


def test_an_object_form_region_value_decodes() -> None:
    """The schema allows `{"value": …}` as well as a bare string; the object
    form failed the whole document's decode until #422."""
    body = _maximal_json()
    _json_object_value(body)
    back = json_reader.read(json.dumps(body).encode())
    assert back.constraints[0].regions[0].keys[0].codes() == frozenset({"PARENT"})
    assert back.lost == ()


def test_sections_names_every_section_the_reader_reads() -> None:
    assert set(SECTIONS) == {field.name for field in msgspec.structs.fields(StructureDataJson)}


def _json_sections() -> list[str]:
    schema = json.loads((ROOT / "tests/schemas/sdmx_json/2.0/sdmx-json-structure-schema.json").read_text())
    return sorted(schema["properties"]["data"]["properties"])


_READ_KEYS = {field.encode_name for field in msgspec.structs.fields(StructureDataJson)}


def test_the_unread_sections_are_the_unmodelled_3_0_classes() -> None:
    """The JSON schema's sections less what the reader reads: SDMX-ML 3.0's unmodelled list, exactly."""
    unread = [key for key in _json_sections() if key not in _READ_KEYS]
    assert sorted(_singular(key) for key in unread) == _UNMODELLED["3.0"]


@pytest.mark.parametrize("key", [key for key in _json_sections() if key not in _READ_KEYS])
def test_an_unread_section_is_refused_entry_by_entry(key: str) -> None:
    body = _maximal_json()
    data = body["data"]
    assert isinstance(data, dict)
    data[key] = [{"id": "X", "agencyID": "AG", "version": "1.0"}, {"id": "Y", "agencyID": "AG", "version": "2.0"}]
    back = json_reader.read(json.dumps(body).encode())
    assert [r.urn.rsplit("=", 1)[1] for r in back.refused] == ["AG:X(1.0)", "AG:Y(2.0)"]
    assert all(r.reason == f"sdmxlib does not model {_singular(key)}" for r in back.refused)
    assert back.codelists, "the rest of the document is read"


# ── keys no struct declares, found without a row ──────────────────────────


@pytest.mark.parametrize(
    ("where", "construct"),
    [
        (lambda body: _section(body, "codelists")[0], "CodelistJson.xVendorFlag"),
        (lambda body: _list(_section(body, "codelists")[0], "codes")[0], "CodeJson.xVendorFlag"),
        (
            lambda body: _obj(_list(_section(body, "conceptSchemes")[0], "concepts")[1], "coreRepresentation"),
            "Representation.xVendorFlag",
        ),
    ],
    ids=["artefact", "item", "nested"],
)
def test_a_key_no_struct_declares_is_reported_wherever_it_sits(
    where: Callable[[dict[str, object]], object], construct: str
) -> None:
    """No row names a vendor's key: the raw walk beside the structs finds it at any depth (#422)."""
    body = _maximal_json()
    target = where(body)
    assert isinstance(target, dict)
    target["xVendorFlag"] = True
    back = json_reader.read(json.dumps(body).encode())
    (gap,) = back.lost
    assert (gap.construct, gap.count) == (construct, 1)
    assert back.refused == ()


def test_the_walk_counts_each_occurrence() -> None:
    body = _maximal_json()
    for code in _list(_section(body, "codelists")[0], "codes"):
        assert isinstance(code, dict)
        code["x-vendor"] = 1
    (gap,) = json_reader.read(json.dumps(body).encode()).lost
    assert (gap.construct, gap.count) == ("CodeJson.x-vendor", len(_list(_section(body, "codelists")[0], "codes")))


def test_the_walk_caches_each_struct_class_once() -> None:
    """The cost guard (#422): wire names are read once per struct class, not per object.

    Per object, they were most of the walk — a 100,000-code codelist read in
    8.7 s took 16.8 s; cached, the walk is within noise of nothing. The walk
    runs when `lost` is first read (#461), so each read here asks for it.
    """
    from sdmxlib.formats.sdmx_json import _losses

    data = json_writer.write(maximal.message())
    _losses._declared.cache_clear()
    _ = json_reader.read(data).lost
    first = _losses._declared.cache_info()
    _ = json_reader.read(data).lost
    second = _losses._declared.cache_info()
    assert first.misses == first.currsize, "one miss per struct class"
    assert second.misses == first.misses, "a second read computes nothing"
    assert second.hits > first.hits
