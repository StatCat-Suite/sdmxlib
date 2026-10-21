"""A code whose parent is not an NCName id cannot be written as SDMX-ML 3.0.0 (#432).

3.0.0 types `<Code><Parent>` as `SingleNCNameIDType` while a code's own `@id`
is any `IDType`, so `<Code id="_T">` is valid and a child of it is not. 3.1.0
fixed the erratum; 2.1 never had it. The registry sweep found it at every
registry with code hierarchies — `_T`, SDMX's "total", is the usual parent —
and the writer emitted it invalid. It is refused for 3.0 now, pointing at 3.1.
"""

import pytest
from lxml import etree

from sdmxlib.formats import serialize
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support import repo
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


def _codelist(parent: str, *, children: int = 1) -> StructureMessage:
    codes = [Code(id=parent, name={"en": "p"})]
    codes += [Code(id=f"C{i}", name={"en": "c"}, parent_id=parent) for i in range(children)]
    return StructureMessage(codelists=[Codelist(id="CL", maintainer="AG", name={"en": "cl"}, codes=codes)])


def test_the_erratum_is_still_in_the_vendored_3_0_schema() -> None:
    """The refusal exists because of one schema line. If a refresh fixes it,
    this fails and the refusal should go."""
    xsd = "{http://www.w3.org/2001/XMLSchema}"
    tree = etree.parse(str(repo.ROOT / "tests" / "schemas" / "sdmx_ml30" / "SDMXStructureCodelist.xsd"))
    parents = [e.get("type") for e in tree.iter(f"{xsd}element") if e.get("name") == "Parent"]
    assert parents == ["common:SingleNCNameIDType"]


def test_a_leading_underscore_parent_is_refused_for_3_0_naming_3_1() -> None:
    with pytest.raises(ValidationError) as caught:
        serialize(_codelist("_T"), version="3.0")
    (issue,) = caught.value.issues
    assert issue.path == "Codelist:CL.codes[].parent_id"
    assert "C0 (parent '_T')" in issue.message
    assert "Write SDMX-ML 3.1 or 2.1 instead" in issue.message


@pytest.mark.parametrize("version", ["2.1", "3.1"])
def test_the_same_codelist_is_valid_where_the_erratum_is_not(version: str) -> None:
    assert_valid_sdmx_ml(serialize(_codelist("_T"), version=version), structure_schema(version))


def test_an_ncname_parent_is_written_as_3_0() -> None:
    """Discrimination: the refusal is about the parent's first character."""
    assert_valid_sdmx_ml(serialize(_codelist("T"), version="3.0"), structure_schema("3.0"))


def test_one_issue_per_codelist_however_many_codes() -> None:
    """A registry codelist can carry thousands; the issue counts them."""
    with pytest.raises(ValidationError) as caught:
        serialize(_codelist("_T", children=5), version="3.0")
    (issue,) = caught.value.issues
    assert issue.message.startswith("5 code(s)")
    assert "and 2 more" in issue.message
