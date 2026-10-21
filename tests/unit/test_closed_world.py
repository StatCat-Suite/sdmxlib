"""An SDMX-ML artefact holding an element its schema allows nowhere is refused (#436).

The readers fetch the children they know by qualified name, so anything else
vanished. Eurostat's 3.0 AgencyScheme names itself with ``<str:Name>`` where
the schema says ``<com:Name>``; the reader read no name and ``validate()`` then
refused the scheme for having none. The check now names the element.

Four kinds of test, because a closed world fails two ways:

- **It catches** — the Eurostat shape and an injected ``<str:Bogus>`` in each
  generation are refused, naming the element, its parent and its line.
- **It does not over-catch** — every vendored fixture and a maximal writer
  round trip read with nothing refused. A table missing one substitution
  group or extension would show up here, not in production.
- **The table is the schema's** — the shipped module equals a fresh run of
  the generator, and knows the cases above by name.
- **Each catching test can fail** — `test_the_eurostat_refusal_depends_on_the_table`
  removes the one entry it relies on and watches the refusal disappear.
"""

import gzip
import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Any

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import _refuse, parse
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_ml import _allowed_children, _closed_world, _losses
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.validation import _UNWRITABLE, SdmxConformanceError
from tests.support import maximal
from tests.support.repo import ROOT
from tests.support.roundtrip import FIXTURES, as_structure

pytestmark = pytest.mark.unit

_TOOL = ROOT / "tools" / "generate_allowed_children.py"
_spec = importlib.util.spec_from_file_location("generate_allowed_children", _TOOL)
assert _spec is not None
assert _spec.loader is not None
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)

_STR = {
    "2.1": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "3.0": "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure",
    "3.1": "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/structure",
}


# ── Eurostat's shape ──────────────────────────────────────────────────────────


#: What Eurostat's 3.0 AgencyScheme also carries: 3.0 fixes an organisation
#: scheme's version and removed ``isFinal``, so prohibits both (#446).
_EUROSTAT_ATTRIBUTES = ' version="1.0" isFinal="false"'


def _eurostat(name_prefix: str, attributes: str = "") -> bytes:
    """A 3.0 message: Eurostat's AgencyScheme, named with *name_prefix*, and a codelist.

    Without *attributes* only its name is Eurostat's, so the element tests see
    the element alone; `_EUROSTAT_ATTRIBUTES` gives it the rest of the real shape.
    """
    return f"""<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message"
  xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure"
  xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common">
  <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test>
    <mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id="X"/></mes:Header>
  <mes:Structures>
    <str:AgencySchemes>
      <str:AgencyScheme id="AGENCIES" agencyID="SDMX"{attributes}>
        <{name_prefix}:Name xml:lang="en">Standard Agencies</{name_prefix}:Name>
        <str:Agency id="EUROSTAT">
          <{name_prefix}:Name xml:lang="en">Statistical Office of the European Union</{name_prefix}:Name>
        </str:Agency>
      </str:AgencyScheme>
    </str:AgencySchemes>
    <str:Codelists>
      <str:Codelist id="CL_FREQ" agencyID="SDMX" version="1.0">
        <com:Name xml:lang="en">Frequency</com:Name>
        <str:Code id="A"><com:Name xml:lang="en">Annual</com:Name></str:Code>
      </str:Codelist>
    </str:Codelists>
  </mes:Structures>
</mes:Structure>""".encode()


def test_eurostat_agency_scheme_with_str_name_is_refused() -> None:
    with pytest.raises(SdmxConformanceError, match=r"str:Name"):
        parse(_eurostat("str"))


def test_strict_false_returns_the_rest_and_names_the_element_and_line() -> None:
    msg = as_structure(parse(_eurostat("str"), strict=False))
    assert [cl.id for cl in msg.codelists] == ["CL_FREQ"]
    assert list(msg.agency_schemes) == []
    (refusal,) = msg.refused
    assert refusal.artefact_class == "AgencyScheme"
    assert refusal.reason.startswith("<str:Name>: not allowed in <str:AgencyScheme> (line 9)")
    assert "com:Name" in refusal.reason  # names what the schema wants instead


def test_eurostat_agency_scheme_with_com_name_reads() -> None:
    msg = as_structure(parse(_eurostat("com")))
    assert msg.refused == ()
    assert msg.agency_schemes[0].name == InternationalString(en="Standard Agencies")


def test_the_eurostat_refusal_depends_on_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sabotage: allow ``str:Name`` under AgencyScheme and the refusal must vanish.

    The catching tests are only evidence if this one passes — if the refusal
    survived the table allowing the element, it came from somewhere else
    (``validate()`` refusing a nameless scheme, say) and the check is
    untested. Agency is allowed it too, else the walk would refuse there.
    """
    table = dict(_closed_world._ALLOWED_CLARK["3.0"])
    for parent in ("AgencyScheme", "Agency"):
        table[f"{{{_STR['3.0']}}}{parent}"] |= {f"{{{_STR['3.0']}}}Name"}
    monkeypatch.setitem(_closed_world._ALLOWED_CLARK, "3.0", table)
    msg = as_structure(parse(_eurostat("str"), strict=False))
    assert not any("not allowed" in r.reason for r in msg.refused)


def test_without_the_check_the_name_vanishes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect itself, kept visible: unchecked, the reader just loses the name."""
    monkeypatch.setattr(_refuse, "check_artefact", lambda _el: None)
    msg = as_structure(parse(_eurostat("str"), strict=False))
    assert not any("not allowed" in r.reason for r in msg.refused)


# ── Attributes (#446) ─────────────────────────────────────────────────────────


def test_eurostat_agency_scheme_with_its_version_is_refused() -> None:
    """The real shape: the attribute is met first, on the scheme's own element."""
    msg = as_structure(parse(_eurostat("com", _EUROSTAT_ATTRIBUTES), strict=False))
    (refusal,) = msg.refused
    assert refusal.artefact_class == "AgencyScheme"
    assert refusal.reason.startswith("<str:AgencyScheme>: @version is not allowed on it (line 8)")
    assert "@agencyID" in refusal.reason  # names what the schema allows instead
    assert [cl.id for cl in msg.codelists] == ["CL_FREQ"]


def test_the_attribute_refusal_depends_on_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sabotage: allow both attributes on AgencyScheme and the refusal must vanish."""
    table = dict(_closed_world._ATTRIBUTES_CLARK["3.0"])
    scheme = f"{{{_STR['3.0']}}}AgencyScheme"
    table[scheme] |= {"version", "isFinal"}
    monkeypatch.setitem(_closed_world._ATTRIBUTES_CLARK, "3.0", table)
    assert as_structure(parse(_eurostat("com", _EUROSTAT_ATTRIBUTES), strict=False)).refused == ()


def _with_attribute(version: str, fixture: str, xpath: str, name: str) -> tuple[bytes, int]:
    """*fixture* with attribute *name* set on the first node *xpath* finds, and its line."""
    tree = etree.parse(str(FIXTURES / f"sdmx_ml{version.replace('.', '')}" / fixture))
    (target, *_) = tree.xpath(xpath, namespaces={"str": _STR[version]})
    assert isinstance(target, etree._Element)
    target.set(name, "x")
    data = etree.tostring(tree)
    # Serialising moves lines, so the line is the reparsed element's, as `_inject` reads it.
    (reparsed, *_) = [el for el in etree.fromstring(data).iter() if name in el.attrib]
    return data, reparsed.sourceline or 0


@pytest.mark.parametrize(
    ("version", "fixture", "xpath", "element", "cls"),
    [
        pytest.param("2.1", "minimal.xml", "//str:Codelist/str:Code", "str:Code", "Codelist", id="2.1-code"),
        pytest.param("3.0", "minimal.xml", "//str:Codelist", "str:Codelist", "Codelist", id="3.0-codelist"),
        pytest.param("3.1", "complete.xml", "//str:Dimension", "str:Dimension", "DataStructure", id="3.1-dimension"),
    ],
)
def test_a_misspelt_attribute_is_refused(version: str, fixture: str, xpath: str, element: str, cls: str) -> None:
    """``validfrom`` for ``validFrom``: read past, its value lost, until the attributes were checked."""
    data, line = _with_attribute(version, fixture, xpath, "validfrom")
    (refusal,) = as_structure(parse(data, strict=False)).refused
    assert refusal.artefact_class == cls
    assert refusal.reason.startswith(f"<{element}>: @validfrom is not allowed on it (line {line})")


def test_xsi_attributes_are_always_allowed() -> None:
    data, _ = _with_attribute("2.1", "minimal.xml", "//str:Codelist", "{http://www.w3.org/2001/XMLSchema-instance}type")
    assert as_structure(parse(data, strict=False)).refused == ()


def test_an_element_with_any_attribute_is_open() -> None:
    """2.1's ``str:Key`` declares ``xs:anyAttribute``: nothing on it is checked."""
    assert _allowed_children.OPEN_ATTRIBUTES["2.1"] == frozenset({"str:Key"})
    assert f"{{{_STR['2.1']}}}Key" not in _closed_world._ATTRIBUTES_CLARK["2.1"]


# ── An injected <str:Bogus> ───────────────────────────────────────────────────


def _inject(version: str, fixture: str, xpath: str) -> tuple[bytes, int]:
    """*fixture* with ``<str:Bogus/>`` appended to the first node *xpath* finds, and its line."""
    tree = etree.parse(str(FIXTURES / f"sdmx_ml{version.replace('.', '')}" / fixture))
    (target, *_) = tree.xpath(xpath, namespaces={"str": _STR[version]})
    assert isinstance(target, etree._Element)
    etree.SubElement(target, f"{{{_STR[version]}}}Bogus")
    data = etree.tostring(tree)
    reparsed = etree.fromstring(data).iter(f"{{{_STR[version]}}}Bogus")
    return data, next(reparsed).sourceline or 0


@pytest.mark.parametrize(
    ("version", "fixture", "xpath", "parent", "cls"),
    [
        pytest.param("2.1", "minimal.xml", "//str:Codelist/str:Code", "str:Code", "Codelist", id="2.1-code"),
        pytest.param("2.1", "minimal.xml", "//str:Codelist", "str:Codelist", "Codelist", id="2.1-codelist"),
        pytest.param("3.1", "complete.xml", "//str:Dimension", "str:Dimension", "DataStructure", id="3.1-dimension"),
    ],
)
def test_an_injected_element_is_refused(version: str, fixture: str, xpath: str, parent: str, cls: str) -> None:
    data, line = _inject(version, fixture, xpath)
    with pytest.raises(SdmxConformanceError, match=r"str:Bogus"):
        parse(data)
    refused = as_structure(parse(data, strict=False)).refused
    assert [(r.artefact_class, r.reason.split(";")[0]) for r in refused] == [
        (cls, f"<str:Bogus>: not allowed in <{parent}> (line {line})")
    ]


def test_comments_and_processing_instructions_are_not_elements() -> None:
    tree = etree.parse(str(FIXTURES / "sdmx_ml21" / "minimal.xml"))
    (code, *_) = tree.xpath("//str:Code", namespaces={"str": _STR["2.1"]})
    assert isinstance(code, etree._Element)
    code.append(etree.Comment("a note"))
    code.append(etree.ProcessingInstruction("app", "x"))
    assert as_structure(parse(etree.tostring(tree))).refused == ()


# ── No false positives ────────────────────────────────────────────────────────


def _fixtures() -> list[Path]:
    paths = [p for d in ("sdmx_ml21", "sdmx_ml30", "sdmx_ml31") for p in sorted((FIXTURES / d).iterdir())]
    # 8 + 10 + 9 today; exact, so a fixture directory that stops being found
    # fails here rather than leaving the next test checking nothing.
    assert len(paths) == 27
    return paths


@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_vendored_fixture_reads_with_nothing_refused(path: Path) -> None:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    assert as_structure(parse(data, strict=False)).refused == ()


_FORMATS = {
    "2.1": (ml21_writer, ml21_reader),
    "3.0": (ml30_writer, ml30_reader),
    "3.1": (ml31_writer, ml31_reader),
}


@pytest.mark.parametrize("version", sorted(_FORMATS))
def test_the_maximal_message_round_trips_with_nothing_refused(version: str) -> None:
    """What our writers emit, at its widest, is all in the table.

    Cleared of what each target refuses to write, as `test_field_coverage`
    does — the question here is the reader's, not the writer's.
    """
    writer, reader = _FORMATS[version]
    original = maximal.message()
    # Slot names known only at runtime: the keyword expansion cannot be
    # checked statically, so the one annotated local `test_field_coverage` uses.
    cleared: dict[str, Any] = dict.fromkeys(_UNWRITABLE.get(version, {}), ())
    # And of what the target leaves out whole — 2.1's value list and what names it.
    original = without_dropped(attrs.evolve(original, **cleared), version)
    back = reader.read(writer.write(original))
    assert back.refused == ()
    assert len(back.all_artefacts()) == len(original.all_artefacts())


# ── The table ─────────────────────────────────────────────────────────────────


def test_the_shipped_table_is_what_the_schemas_say() -> None:
    """Drift guard: regenerate with ``just generate-allowed-children``."""
    shipped = (ROOT / "src" / "sdmxlib" / "formats" / "sdmx_ml" / "_allowed_children.py").read_text(encoding="utf-8")
    assert shipped == generator.render()


def test_the_table_knows_the_cases_that_matter() -> None:
    allowed = _allowed_children.ALLOWED
    assert "com:Name" in allowed["3.0"]["str:AgencyScheme"]
    assert "str:Name" not in allowed["3.0"]["str:AgencyScheme"]
    assert "str:Code" in allowed["2.1"]["str:Codelist"]
    # Substitution group, transitively: Code ← GeoRefCode (abstract, so
    # absent) ← GeoGridCode.
    assert "str:GeoGridCode" in allowed["3.0"]["str:Codelist"]
    assert "str:GeoRefCode" not in allowed["3.0"]["str:Codelist"]
    # Restriction replaces: AgencySchemeType restricts OrganisationSchemeType,
    # whose ``Organisation`` group would also admit DataProvider.
    assert "str:DataProvider" not in allowed["3.0"]["str:AgencyScheme"]
    # Extension appends: DimensionList's own Dimension, plus AnnotableType's
    # Annotations from the base.
    assert {"com:Annotations", "str:Dimension"} <= allowed["3.1"]["str:DimensionList"]
    # A leaf admits nothing.
    assert allowed["3.0"]["com:Name"] == frozenset()
    # A restriction's maxOccurs="0" prohibits (#422): 3.x's
    # MetadataAttributeUsageBaseType keeps ConceptIdentity and
    # LocalRepresentation from its base only to forbid them.
    for version in ("3.0", "3.1"):
        usage = allowed[version]["str:MetadataAttributeUsage"]
        assert "str:MetadataAttributeReference" in usage
        assert not {"str:ConceptIdentity", "str:LocalRepresentation"} & usage


@pytest.mark.parametrize("version", ["3.0", "3.1"])
@pytest.mark.parametrize("child", ["ConceptIdentity", "LocalRepresentation"])
def test_a_prohibited_child_is_refused(version: str, child: str) -> None:
    """An element the schema forbids at maxOccurs="0" is refused, not read past (#422)."""
    writer = {"3.0": ml30_writer, "3.1": ml31_writer}[version]
    doc = etree.fromstring(writer.write(maximal.message()))
    (usage, *_) = doc.iter(f"{{{_STR[version]}}}MetadataAttributeUsage")
    etree.SubElement(usage, f"{{{_STR[version]}}}{child}")
    refused = as_structure(parse(etree.tostring(doc), strict=False)).refused
    assert [(r.artefact_class, r.reason.split(" (line")[0]) for r in refused] == [
        ("DataStructure", f"<str:{child}>: not allowed in <str:MetadataAttributeUsage>")
    ]


def test_a_parent_with_xs_any_is_open() -> None:
    """``com:StructuredText`` carries XHTML via ``xs:any``.

    Not reachable from ``mes:Structures`` in any version, so the shipped
    `OPEN` sets are empty; the generator's unfiltered view is where it shows.
    """
    _, is_open = generator.contents(generator.Schema(ROOT / "tests" / "schemas" / "sdmx_ml30"))
    assert "{http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common}StructuredText" in is_open
    shipped = _allowed_children.OPEN
    assert shipped == {"2.1": frozenset(), "3.0": frozenset(), "3.1": frozenset()}


# ── The frame: header and containers (#447) ───────────────────────────────────

type _Mutate = Callable[[etree._Element, dict[str, str]], None]

_MES = {
    "2.1": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "3.0": "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message",
    "3.1": "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/message",
}


def _framed(version: str, mutate: _Mutate) -> bytes:
    """The maximal message in *version*, written and changed by *mutate*(document, namespaces)."""
    writer, _ = _FORMATS[version]
    doc = etree.fromstring(writer.write(maximal.message()))
    mutate(doc, {"mes": _MES[version], "str": _STR[version]})
    return etree.tostring(doc)


def _one(doc: etree._Element, xpath: str, ns: dict[str, str]) -> etree._Element:
    (found, *_) = doc.xpath(xpath, namespaces=ns)
    assert isinstance(found, etree._Element)
    return found


def _stray_in_codelists(doc: etree._Element, ns: dict[str, str]) -> None:
    etree.SubElement(_one(doc, "//str:Codelists", ns), f"{{{ns['str']}}}Bogus")


def _misplaced_scheme(doc: etree._Element, ns: dict[str, str]) -> None:
    _one(doc, "//str:Codelists", ns).append(_one(doc, "//str:ConceptScheme", ns))


def _unknown_container(doc: etree._Element, ns: dict[str, str]) -> None:
    container = etree.SubElement(_one(doc, "//mes:Structures", ns), f"{{{ns['str']}}}Bogus")
    etree.SubElement(container, f"{{{ns['str']}}}Codelist", id="X", agencyID="AG", version="1.0")


def _foreign(doc: etree._Element, ns: dict[str, str]) -> None:
    etree.SubElement(_one(doc, "//mes:Structures", ns), "{urn:example}Bogus")


def _header(doc: etree._Element, ns: dict[str, str]) -> None:
    etree.SubElement(_one(doc, "//mes:Header", ns), f"{{{ns['mes']}}}Bogus")


def _container_attribute(doc: etree._Element, ns: dict[str, str]) -> None:
    _one(doc, "//str:Codelists", ns).set("bogus", "1")


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
@pytest.mark.parametrize(
    ("mutate", "cls", "place", "reason"),
    [
        pytest.param(
            _stray_in_codelists, "str:Bogus", "mes:Structures/str:Codelists/str:Bogus[1]",
            "<str:Bogus>: not allowed in <str:Codelists>", id="stray-in-container",
        ),
        pytest.param(
            _misplaced_scheme, "str:ConceptScheme", "mes:Structures/str:Codelists/str:ConceptScheme[1]",
            "<str:ConceptScheme>: not allowed in <str:Codelists>", id="misplaced-artefact",
        ),
        pytest.param(
            _unknown_container, "str:Bogus", "mes:Structures/str:Bogus[1]",
            "<str:Bogus>: not allowed in <mes:Structures>", id="unknown-container",
        ),
        pytest.param(
            _foreign, "{urn:example}Bogus", "mes:Structures/{urn:example}Bogus[1]",
            "<{urn:example}Bogus>: not allowed in <mes:Structures>", id="foreign-namespace",
        ),
        pytest.param(_header, "mes:Header", "mes:Header", "<mes:Bogus>: not allowed in <mes:Header>", id="header"),
        pytest.param(
            _container_attribute, "str:Codelists", "mes:Structures/str:Codelists[1]",
            "<str:Codelists>: @bogus is not allowed on it", id="container-attribute",
        ),
    ],
)  # fmt: skip
def test_the_frame_refuses_by_place(version: str, mutate: _Mutate, cls: str, place: str, reason: str) -> None:
    """Outside any artefact, a stray is refused by where it is, once, with the schema's reason (#447).

    Before, these vanished, or were refused as a class "sdmxlib does not model"
    or, for a misplaced artefact, as a reader defect.
    """
    msg = as_structure(parse(_framed(version, mutate), strict=False))
    (refusal,) = msg.refused
    assert (refusal.artefact_class, refusal.urn) == (cls, place)
    assert refusal.reason.startswith(reason)


def test_a_stray_in_the_frame_fails_a_strict_parse() -> None:
    with pytest.raises(SdmxConformanceError, match=r"<str:Bogus>: not allowed in <str:Codelists>"):
        parse(_framed("3.0", _stray_in_codelists))


def test_the_frame_refusals_come_from_the_frame_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sabotage: without `frame_refusals` an empty unknown container vanishes, as it did before #447."""
    monkeypatch.setattr(_losses, "frame_refusals", lambda _structures: ([], set()))
    assert as_structure(parse(_framed("3.0", _empty_container), strict=False)).refused == ()


def _empty_container(doc: etree._Element, ns: dict[str, str]) -> None:
    etree.SubElement(_one(doc, "//mes:Structures", ns), f"{{{ns['str']}}}Bogus")
