"""A code whose parent is not an NCName id cannot be written as SDMX-ML 3.0.0 (#432).

3.0.0 types `<Code><Parent>` as `SingleNCNameIDType` while a code's own `@id`
is any `IDType`, so `<Code id="_T">` is valid and a child of it is not. 3.1.0
fixed the erratum; 2.1 never had it. The registry sweep found it at every
registry with code hierarchies — `_T`, SDMX's "total", is the usual parent —
and the writer emitted it invalid. It is refused for 3.0 now, pointing at 3.1.

.Stat NSI serves such parents in 3.0.0 and FMR accepts them, so a caller can
write them on purpose with `allow={"non-ncname-parent"}`, which skips this one
check (GH-398).
"""

from collections.abc import Collection
from typing import cast, get_args

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize, to_xml
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import OrganisationUnitScheme
from sdmxlib.model.validation import DEVIATIONS, Deviation, ValidationError, check_serialisable
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


def _with_unit_scheme(msg: StructureMessage) -> StructureMessage:
    """Adds a second 3.0 violation: an organisation unit scheme at a version 3.x cannot carry."""
    scheme = OrganisationUnitScheme(id="OU", maintainer="AG", name={"en": "ou"}, version="2.0")
    return attrs.evolve(msg, organisation_unit_schemes=[scheme])


def test_the_allowance_writes_the_parents_as_nsi_and_fmr_exchange_them() -> None:
    """Written, and read back with every parent; schema-invalid at `<Parent>` and nowhere else."""
    written = to_xml(_codelist("_T", children=2), version="3.0", allow={"non-ncname-parent"})
    schema = structure_schema("3.0")
    assert not schema.validate(etree.fromstring(written))
    assert {"Parent" in error.message for error in schema.error_log} == {True}
    read_back = parse(written)
    assert isinstance(read_back, StructureMessage)
    (codelist,) = read_back.codelists
    assert {code.id: code.parent_id for code in codelist.codes} == {"_T": None, "C0": "_T", "C1": "_T"}


def test_the_allowance_skips_only_the_parent_check() -> None:
    msg = _with_unit_scheme(_codelist("_T"))
    with pytest.raises(ValidationError) as refused:
        check_serialisable(msg, version="3.0")
    assert {issue.path for issue in refused.value.issues} == {
        "Codelist:CL.codes[].parent_id",
        "OrganisationUnitScheme:OU.version",
    }
    with pytest.raises(ValidationError) as allowed:
        check_serialisable(msg, version="3.0", allow={"non-ncname-parent"})
    assert {issue.path for issue in allowed.value.issues} == {"OrganisationUnitScheme:OU.version"}


def test_an_unknown_deviation_is_refused() -> None:
    # An untyped caller, such as a config file's list of names: the checker
    # would refuse the literal, so the cast stands in for that caller.
    names = cast("Collection[Deviation]", ["ncname"])
    with pytest.raises(ValueError, match=r"unknown deviation\(s\) \['ncname'\]; the known ones are"):
        check_serialisable(_codelist("T"), version="3.0", allow=names)


def test_the_deviation_set_matches_the_type() -> None:
    assert frozenset(get_args(Deviation.__value__)) == DEVIATIONS
