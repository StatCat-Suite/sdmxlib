"""`xs:boolean` is read one way everywhere: ``1`` and ``0`` are booleans too (#458).

Mutation on the 2.1 reader left `leveled`'s default untouched by any test, and
reading why found four rules for the same schema type. ``leveled="1"`` read
false and ``isInclusive="0"`` read true, while `isExtension` and `isIncluded`
already took both. Every read now goes through `_common.flag`.
"""

import re
from pathlib import Path

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.sdmx_ml._closed_world import SdmxConformanceError
from sdmxlib.formats.sdmx_ml._common import flag, optional_flag, required_flag
from sdmxlib.model.constraint import ConstraintAttachment, DataConstraint, QueryableDataSource
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.repo import ROOT
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_FIXTURES = ROOT / "tests" / "fixtures"


def _el(**attrs: str) -> etree._Element:
    el = etree.Element("x")
    for name, value in attrs.items():
        el.set(name, value)
    return el


@pytest.mark.parametrize(("lexical", "value"), [("true", True), ("1", True), ("false", False), ("0", False)])
def test_every_lexical_form_reads_as_its_value(lexical: str, *, value: bool) -> None:
    el = _el(a=lexical)
    assert flag(el, "a") is value
    assert flag(el, "a", default=not value) is value
    assert optional_flag(el, "a") is value
    assert required_flag(el, "a") is value


def test_an_absent_attribute_reads_as_the_schema_default() -> None:
    assert flag(_el(), "a") is False
    assert flag(_el(), "a", default=True) is True
    assert optional_flag(_el(), "a") is None


def test_an_absent_required_attribute_is_refused() -> None:
    with pytest.raises(SdmxConformanceError):
        required_flag(_el(), "a")


def _read(path: Path, old: str, new: str) -> sl.StructureMessage:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{path.name} no longer carries {old}"
    return as_structure(sl.parse(text.replace(old, new).encode()))


def test_a_2_1_hierarchy_leveled_by_1_is_leveled() -> None:
    path = _FIXTURES / "sdmx_ml21" / "with_hierarchy.xml"
    worded = _read(path, 'leveled="true"', 'leveled="true"')
    numeric = _read(path, 'leveled="true"', 'leveled="1"')
    assert [h.has_formal_levels for h in numeric.hierarchies] == [h.has_formal_levels for h in worded.hierarchies]
    assert any(h.has_formal_levels for h in numeric.hierarchies), "the case needs a leveled hierarchy"


def test_a_3_0_hierarchy_with_formal_levels_by_1_has_them() -> None:
    path = _FIXTURES / "sdmx_ml30" / "with_multiple_hierarchies.xml"
    numeric = _read(path, 'hasFormalLevels="false"', 'hasFormalLevels="1"')
    assert all(h.has_formal_levels for h in numeric.hierarchies)


def test_a_2_1_is_final_of_1_is_final() -> None:
    path = _FIXTURES / "sdmx_ml21" / "minimal.xml"
    final = 'id="CL_FREQ" agencyID="SDMX" version="1.0" isFinal="true"'
    worded = _read(path, final, final)
    numeric = _read(path, final, final.replace('"true"', '"1"'))
    assert numeric.codelists["CL_FREQ"].legacy_is_final is worded.codelists["CL_FREQ"].legacy_is_final is True


# ── no reader grows a fifth rule ──────────────────────────────────────────

#: An attribute compared against a boolean word, rather than read by `flag`.
_INLINE = re.compile(r'''(==|!=)\s*"(true|false)"|in \("true"|not in \("false"''')


def test_the_inline_pattern_catches_each_rule_it_replaced() -> None:
    """Without this the guard below could pass by matching nothing."""
    for rule in (
        'el.get("leveled", "false").lower() == "true"',
        'el.get("isInclusive", "true") != "false"',
        'el.get("isExtension", "false").strip() in ("true", "1")',
        'el.get("include", "true").strip().lower() not in ("false", "0")',
    ):
        assert _INLINE.search(rule), rule


def test_no_reader_compares_an_attribute_against_a_boolean_word() -> None:
    readers = [
        path
        for path in (ROOT / "src" / "sdmxlib" / "formats").rglob("*.py")
        if "writer" not in path.name and path.parent.name.startswith("sdmx_ml")
    ]
    assert len(readers) >= 8, "the scan lost its subject"  # a floor: reader modules come and go
    offenders = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in readers
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _INLINE.search(line)
    ]
    assert not offenders, f"read `xs:boolean` through `_common.flag`: {offenders}"


# ── a required flag is refused when absent, not read as false ─────────────


_FLOW = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:DF(1.0)")


@pytest.mark.parametrize("attribute", ["isRESTDatasource", "isWebServiceDatasource"])
@pytest.mark.parametrize("version", ["2.1", "3.0"])
def test_a_queryable_data_source_without_its_required_flag_is_refused(attribute: str, version: str) -> None:
    """`use="required"` in every XSD; read as false, a source that never said lost nothing visible."""
    source = QueryableDataSource(
        data_url="https://example.org/rest", is_rest_datasource=True, is_web_service_datasource=False
    )
    constraint = DataConstraint(
        id="DC",
        maintainer="AG",
        name={"en": "DC"},
        attachment=ConstraintAttachment(dataflows=(Ref[object].unresolved(_FLOW),), queryable_data_sources=(source,)),
    )
    written = sl.serialize(StructureMessage(constraints=[constraint]), version=version)
    assert f'{attribute}="'.encode() in written, "the case needs the attribute to remove"
    stripped = re.sub(rf' {attribute}="[^"]*"'.encode(), b"", written)
    with pytest.raises(SdmxConformanceError, match=attribute):
        sl.parse(stripped)
