"""The version a *reference* names, per target format (#429).

`model/version.py` answers two questions. `unwritable` asks whether an
artefact's own version fits a format's grammar; `unreferenceable` asks the
same of a version a reference names, which may also late-bind (`1.0+.0`).
Nothing asked the second until #429, and every writer copied reference
versions through: IMF's wildcards became 20,983 schema-invalid 2.1
attributes.

The grammar is checked differentially, against the vendored schemas rather
than against `compliance`, which `unreferenceable` calls — comparing the two
would compare the code with itself.
"""

import functools
import json
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st
from lxml import etree

from sdmxlib.formats import serialize
from sdmxlib.formats.sdmx_ml21.reader import read as read_21
from sdmxlib.formats.sdmx_ml21.writer import write as write_21
from sdmxlib.model.concept import Concept
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError
from sdmxlib.model.version import unreferenceable
from tests.support import repo

pytestmark = pytest.mark.unit

_SCHEMAS = repo.ROOT / "tests" / "schemas"
_XSD = "{http://www.w3.org/2001/XMLSchema}"


def _xsd_patterns(path: str, simple_type: str) -> list[re.Pattern[str]]:
    tree = etree.parse(str(_SCHEMAS / path))
    found = tree.find(f".//{_XSD}simpleType[@name='{simple_type}']")
    assert found is not None, f"{simple_type} is gone from {path}: has the schema been refreshed?"
    # XSD patterns are anchored at both ends implicitly.
    return [re.compile(f"^(?:{p.get('value')})$") for p in found.iter(f"{_XSD}pattern")]


@functools.cache
def _oracles() -> dict[str, list[re.Pattern[str]]]:
    """Target → the schema's own patterns, each matched against a whole reference."""
    json_type = json.loads((_SCHEMAS / "sdmx_json" / "2.1" / "sdmx-json-structure-schema.json").read_text())

    def find(node: object) -> str:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "CodelistReferenceType" and isinstance(value, dict) and "pattern" in value:
                    return str(value["pattern"])
                if found := find(value):
                    return found
        elif isinstance(node, list):
            for value in node:
                if found := find(value):
                    return found
        return ""

    return {
        "2.1": _xsd_patterns("sdmx_ml21/SDMXCommonReferences.xsd", "VersionType"),
        "3.0": _xsd_patterns("sdmx_ml30/SDMXCommonReferences.xsd", "UrnReferenceVersionPart"),
        "3.1": _xsd_patterns("sdmx_ml31/SDMXCommonReferences.xsd", "UrnReferenceVersionPart"),
        "json": [re.compile(find(json_type))],
    }


def _schema_accepts(version: str, target: str) -> bool:
    """What the target's schema says of a reference naming `version`."""
    # 2.1 carries it as `Ref@version`, an attribute of its own; the others
    # inside a URN.
    subject = version if target == "2.1" else f"urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL({version})"
    return any(p.match(subject) for p in _oracles()[target])


def test_the_oracles_are_capable_of_disagreeing() -> None:
    """Discrimination: the four schemas really do differ on these."""
    assert _schema_accepts("1.2.3.4", "2.1")
    assert not _schema_accepts("1.2.3.4", "3.0")
    assert _schema_accepts("1+.0.0+", "3.0")
    assert not _schema_accepts("1+.0.0+", "3.1")
    assert not _schema_accepts("1+.0.0+", "json")
    assert _schema_accepts("1.0+.0", "json")
    assert not _schema_accepts("1.0+.0", "2.1")


@pytest.mark.parametrize("target", ["2.1", "3.0", "3.1", "json"])
@given(version=st.text(alphabet="0123456789.+-*~a", max_size=10))
def test_unreferenceable_agrees_with_the_schema(target: str, version: str) -> None:
    assert (unreferenceable(version, target) is None) == _schema_accepts(version, target), version


@pytest.mark.parametrize(
    "version",
    ["1.0", "1.0.0", "1.0.0-draft", "1.2.3.4", "1.03", "1.0+.0", "1+.0.0", "1.0.0+", "1+.0.0+", "*", "~", "1.0+"],
)
@pytest.mark.parametrize("target", ["2.1", "3.0", "3.1", "json"])
def test_unreferenceable_agrees_on_the_cases_that_matter(target: str, version: str) -> None:
    """The registries' own forms, so a regression names one rather than a fuzz string."""
    assert (unreferenceable(version, target) is None) == _schema_accepts(version, target)


def _dataflow(*versions: str) -> StructureMessage:
    flows = [
        Dataflow(
            id=f"DF{i}",
            maintainer="AG",
            name={"en": "f"},
            structure=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD", version=v)),
        )
        for i, v in enumerate(versions)
    ]
    return StructureMessage(dataflows=flows)


def test_every_unreferenceable_version_is_refused_at_once_with_its_path() -> None:
    with pytest.raises(ValidationError) as caught:
        serialize(_dataflow("1.0+.0", "1.0.0-draft", "1.0"), version="2.1")
    issues = caught.value.issues
    # DF1's draft is not refused but left out, flow and all (#424).
    assert [i.path for i in issues] == ["Dataflow:DF0.structure"]
    assert "write SDMX-ML 3.0, SDMX-ML 3.1, SDMX-JSON instead" in issues[0].message


def test_a_version_no_format_accepts_says_so() -> None:
    with pytest.raises(ValidationError, match="no SDMX format accepts it in a reference"):
        serialize(_dataflow("*"), version="3.0")


def test_a_latest_item_reference_omits_its_version_in_2_1() -> None:
    """2.1 wrote `maintainableParentVersion="1.0"` for `"latest"`: a literal the
    caller never gave, schema-valid, so only this sees it. `serialize` now
    refuses the reference (#233); the writer, for a caller writing directly,
    omits the attribute as a maintainable reference always omitted `version`."""
    urn = SdmxUrn.make(
        Concept, package="conceptscheme", artefact_class="Concept", agency="AG", id="CS", version="latest", item_id="C"
    )
    dsd = DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[Dimension(id="DIM", concept=Ref[Concept].unresolved(urn))],
        measures=[Measure(id="OBS_VALUE", concept=Ref[Concept].unresolved(urn))],
    )
    written = write_21(StructureMessage(data_structures=[dsd]))
    refs = etree.fromstring(written).findall(".//Ref[@maintainableParentID='CS']")
    assert len(refs) == 2
    assert [r.get("maintainableParentVersion") for r in refs] == [None, None]


def test_an_omitted_2_1_reference_version_names_1_0() -> None:
    """Every concrete 2.1 ref type defaults ``version`` and ``maintainableParentVersion`` to 1.0 (#476).

    The reader took the omission for "latest": a reference the document pins
    to 1.0 resolved to whatever was newest. Written at 2.0 and stripped, so a
    reader that kept the written version, or took "latest", fails here.
    """
    concept = SdmxUrn.make(
        Concept, package="conceptscheme", artefact_class="Concept", agency="AG", id="CS", version="2.0", item_id="C"
    )
    dsd = DataStructure(
        id="DSD",
        maintainer="AG",
        version="2.0",
        name={"en": "d"},
        dimensions=[Dimension(id="DIM", concept=Ref[Concept].unresolved(concept))],
        measures=[Measure(id="OBS_VALUE", concept=Ref[Concept].unresolved(concept))],
    )
    flow = Dataflow(id="DF", maintainer="AG", name={"en": "f"}, structure=Ref.to(dsd))
    doc = etree.fromstring(write_21(StructureMessage(data_structures=[dsd], dataflows=[flow])))
    stripped = 0
    for ref in doc.iter("Ref"):
        for attribute in ("version", "maintainableParentVersion"):
            if ref.get(attribute) == "2.0":
                del ref.attrib[attribute]
                stripped += 1
    assert stripped == 3, "the structure reference and both concept references"

    back = read_21(etree.tostring(doc))

    (back_flow,) = back.dataflows
    assert back_flow.structure is not None
    assert back_flow.structure.urn.version == "1.0"
    (back_dsd,) = back.data_structures
    for component in (*back_dsd.dimensions, *back_dsd.measures):
        assert component.concept is not None
        assert component.concept.urn.version == "1.0"
