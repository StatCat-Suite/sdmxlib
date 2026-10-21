"""A name whose text is blank in some language names nothing, and is refused (#452).

``TextType`` is ``xs:string``, so an empty ``<com:Name xml:lang="en"/>`` is
schema-valid; but a strict registry treats it as missing. FMR refused a
codelist whose 498,786 codes were named ``{"en": ""}``. Before, the formats
disagreed with each other and with themselves:

* `serialize` wrote such a name, and `parse` of the same 2.1 file refused it;
* SDMX-ML 2.1 dropped an empty language beside a valid one, SDMX-JSON kept it;
* whitespace-only text passed everywhere.

Now a name field refuses blank text in any language as it is set
(`_rules.named`, #492), and the SDMX-ML reader reads an empty element as the
empty text it is, so every path agrees: such a name cannot be built, so it
cannot be written, and a reader refuses the artefact that holds one.
"""

import json

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
import sdmxlib.model.base
from sdmxlib.formats import parse, serialize
from sdmxlib.model._rules import named
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError, ValidationIssue
from tests.support.unchecked import refusal

pytestmark = pytest.mark.unit


def _codelist(code_name: dict[str, str]) -> Codelist:
    return Codelist(id="CL", maintainer="AG", name={"en": "cl"}, codes=[Code(id="A", name=code_name)])


def _refused(name: dict[str, str]) -> ValidationIssue:
    with pytest.raises(ValidationError) as exc:
        Code(id="A", name=name)
    (issue,) = exc.value.issues
    return issue


@pytest.mark.parametrize("text", ["", " ", "\t\n"])
def test_a_blank_name_is_refused_in_its_language(text: str) -> None:
    """Refused as it is set, so `serialize` has nothing to write and `parse` nothing to refuse."""
    issue = _refused({"en": text, "fr": "un"})
    assert issue.path == "Code.name[en]"
    assert "blank" in issue.message


def test_a_name_with_text_passes() -> None:
    assert refusal(lambda: _codelist({"en": "one", "fr": "un"})) == []


def test_sdmx_json_reads_a_blank_name_as_refused() -> None:
    written = json.loads(serialize(StructureMessage(codelists=[_codelist({"en": "one"})]), version="json"))
    written["data"]["codelists"][0]["codes"][0]["name"] = ""
    written["data"]["codelists"][0]["codes"][0]["names"] = {"en": ""}

    msg = parse(json.dumps(written).encode(), strict=False)

    assert isinstance(msg, StructureMessage)
    (refusal,) = msg.refused
    assert "name[en]" in refusal.reason


def test_sdmx_ml_reads_an_empty_name_beside_a_valid_one_as_refused() -> None:
    """2.1 dropped the empty language and kept the code; SDMX-JSON refused it. Now both refuse."""
    doc = etree.fromstring(serialize(StructureMessage(codelists=[_codelist({"en": "one", "fr": "un"})]), version="2.1"))
    (english,) = doc.xpath("//*[local-name()='Code'][@id='A']/*[local-name()='Name'][@xml:lang='en']")
    assert isinstance(english, etree._Element)
    english.text = None

    msg = parse(etree.tostring(doc), strict=False)

    assert isinstance(msg, StructureMessage)
    (refusal,) = msg.refused
    assert "name[en]" in refusal.reason


def test_an_empty_description_is_read_as_written() -> None:
    """Descriptions are optional, so a blank one is no defect: kept, not dropped as 2.1 used to."""
    codelist = Codelist(id="CL", maintainer="AG", name={"en": "cl"}, description={"en": ""})
    back = parse(serialize(StructureMessage(codelists=[codelist]), version="2.1"))
    assert isinstance(back, StructureMessage)
    assert back.codelists[0].description == sl.InternationalString(en="")


@pytest.mark.parametrize("text", ["\x0b", "\x0c", " \x0b "])
def test_a_forbidden_character_is_named_before_blankness(text: str) -> None:
    """Python strips U+000B and U+000C as whitespace, but XML cannot hold them: that is the finding (#452)."""
    assert "which XML cannot represent" in _refused({"en": text}).message


# ── every nameable class refuses as its name is set (#492) ───────────────────

_NAMEABLE_ITEMS = (sl.Code, sl.Concept, sl.Category, sl.DataProvider, sl.DataConsumer, sl.OrganisationUnit)


def _maintainables() -> list[type]:
    import sdmxlib.model as m

    return [
        obj
        for name in m.__all__
        if isinstance(obj := getattr(m, name, None), type)
        and issubclass(obj, sl.model.base.MaintainableArtefact)
        and obj is not sl.model.base.MaintainableArtefact
    ]


@pytest.mark.parametrize("cls", [*_maintainables(), *_NAMEABLE_ITEMS], ids=lambda c: c.__name__)
def test_every_nameable_class_requires_its_name_and_refuses_a_blank_one(cls: type) -> None:
    """Read off the class: required, keyword-only, validated — so no path builds one without."""
    field = attrs.fields_dict(cls)["name"]
    assert (field.validator, field.default, field.kw_only) == (named, attrs.NOTHING, True)


def test_the_sweep_sees_every_nameable_class() -> None:
    """Exact: 30 maintainables and 6 items; a new one changes this and is then swept above."""
    assert (len(_maintainables()), len(_NAMEABLE_ITEMS)) == (30, 6)


def test_an_agency_is_the_nameable_item_left_to_validate() -> None:
    """The same class is the maintainer an artefact names, which carries no name."""
    assert attrs.fields_dict(sl.Agency)["name"].validator is None
    with pytest.raises(ValidationError, match=r"agencies\[A\]\.name"):
        sl.AgencyScheme(id="AGENCIES", maintainer="SDMX", name={"en": "a"}, agencies=[sl.Agency(id="A")])
