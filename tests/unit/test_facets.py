"""A value against a text format: `sdmxlib._facets.FacetChecker` (GH-297).

The checker reads each SDMX text type from ``src/sdmxlib/_facet_types.xsd``,
which holds SDMXCommon.xsd's definitions without their documentation. These
tests hold that copy to the vendored SDMX-ML 3.0 and 3.1 schemas, check the
type mapping against IM §3.6.3.3, and compare the checker with the vendored
schema itself on generated values.
"""

import functools
import itertools
from collections.abc import Iterable
from importlib import resources
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from lxml import etree

from sdmxlib._facets import _XSD_TYPES, FacetChecker
from sdmxlib.model.representation import DataType, Facet, SentinelValue
from tests.support.repo import find_root

pytestmark = pytest.mark.unit

_XS = "http://www.w3.org/2001/XMLSchema"
_SCHEMAS = find_root() / "tests" / "schemas"
_COMMON = {"3.0": _SCHEMAS / "sdmx_ml30" / "SDMXCommon.xsd", "3.1": _SCHEMAS / "sdmx_ml31" / "SDMXCommon.xsd"}
_NAMESPACE = {
    "3.0": "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common",
    "3.1": "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/common",
}


def _shape(element: etree._Element) -> tuple[object, ...]:
    """An XSD element as its name, attributes and children, documentation left out."""
    children = tuple(_shape(child) for child in element if etree.QName(child).localname != "annotation")
    return (etree.QName(element).localname, tuple(sorted(element.attrib.items())), children)


def _definitions(root: etree._Element) -> dict[str, tuple[object, ...]]:
    """Each top-level simple type of a schema, by name, as its shape."""
    return {str(simple.get("name")): _shape(simple) for simple in root.iterfind(f"{{{_XS}}}simpleType")}


def _ours() -> etree._Element:
    return etree.fromstring(resources.files("sdmxlib").joinpath("_facet_types.xsd").read_bytes())


def _differing(ours: dict[str, tuple[object, ...]], theirs: dict[str, tuple[object, ...]]) -> list[str]:
    """The types `ours` defines differently from `theirs`, or that `theirs` lacks."""
    return sorted(name for name, text in ours.items() if theirs.get(name) != text)


# ── the copy of SDMXCommon.xsd ──────────────────────────────────────────────


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_each_type_is_defined_as_sdmx_common_defines_it(version: str) -> None:
    ours = _definitions(_ours())
    assert len(ours) == 24
    assert _differing(ours, _definitions(etree.parse(str(_COMMON[version])).getroot())) == []


def test_a_changed_pattern_in_the_copy_is_found() -> None:
    root = _ours()
    pattern = root.find(f"{{{_XS}}}simpleType[@name='ReportingQuarterType']//{{{_XS}}}pattern")
    assert pattern is not None
    pattern.set("value", ".{5}Q[1-5].*")
    theirs = _definitions(etree.parse(str(_COMMON["3.1"])).getroot())
    assert _differing(_definitions(root), theirs) == ["ReportingQuarterType"]


def test_every_data_type_maps_to_an_xsd_type_but_xhtml() -> None:
    assert {t.value for t in DataType} - set(_XSD_TYPES) == {"XHTML"}


def test_the_types_with_an_xsd_equivalent_map_as_the_im_tabulates() -> None:
    """IM §3.6.3.3's table: an SDMX facet value type and its XML Schema data type."""
    table = {
        "String": "xs:string",
        "BigInteger": "xs:integer",
        "Integer": "xs:int",
        "Long": "xs:long",
        "Short": "xs:short",
        "Decimal": "xs:decimal",
        "Float": "xs:float",
        "Double": "xs:double",
        "Boolean": "xs:boolean",
        "URI": "xs:anyURI",
        "DateTime": "xs:dateTime",
        "Time": "xs:time",
        "GregorianYear": "xs:gYear",
        "GregorianYearMonth": "xs:gYearMonth",
        "GregorianDay": "xs:date",
        "Day": "xs:gDay",
        "MonthDay": "xs:gMonthDay",
        "Month": "xs:gMonth",
        "Duration": "xs:duration",
    }
    assert {name: _XSD_TYPES[name] for name in table} == table


# ── against the vendored schema ─────────────────────────────────────────────


@functools.cache
def _vendored(type_name: str) -> etree.XMLSchema:
    """A schema of one element of the vendored SDMX-ML 3.1 type `type_name`."""
    location = Path(_COMMON["3.1"]).as_uri()
    document = (
        f'<xs:schema xmlns:xs="{_XS}" xmlns:common="{_NAMESPACE["3.1"]}">'
        f'<xs:import namespace="{_NAMESPACE["3.1"]}" schemaLocation="{location}"/>'
        f'<xs:element name="v" type="common:{type_name}"/></xs:schema>'
    )
    return etree.XMLSchema(etree.fromstring(document))


def _vendored_accepts(type_name: str, value: str) -> bool:
    element = etree.Element("v")
    element.text = value
    return _vendored(type_name).validate(etree.ElementTree(element))


#: Each SDMX text type, and the vendored type it is: SDMXCommon.xsd names each ``<name>Type``.
_SDMX_TYPES = sorted(name for name, xsd in _XSD_TYPES.items() if not xsd.startswith("xs:"))

_ZONES = ("", "Z", "+05:00", "-14:00", "+14:01", "+24:00")

#: Every reporting period from these parts: each type's first and last number, and one past each.
_REPORTING_EDGES = tuple(
    f"{year}-{letter}{number}{zone}"
    for year, letter, number, zone in itertools.product(
        ("2020", "999"),
        "ASTQMWDX",
        ("0", "1", "2", "3", "4", "5", "00", "01", "12", "13", "52", "53", "54", "000", "001", "365", "366", "367"),
        _ZONES,
    )
)

#: Characters time periods are made of, for values no structure below builds.
_PERIOD_TEXT = st.text(alphabet="0123456789-/:+.ZTPYMDHSWQAa ", max_size=26)

#: A time range: a date, perhaps a time and zone, and a duration, the date running past its month's last day.
_RANGE = st.builds(
    "{}-{:02d}-{:02d}{}{}/{}".format,
    st.sampled_from(["2020", "2021", "1900", "2000"]),
    st.integers(0, 13),
    st.integers(0, 32),
    st.sampled_from(["", "T00:00:00", "T24:00:00", "T23:59:59.5", "T25:00:00"]),
    st.sampled_from(_ZONES),
    st.sampled_from(["P1M", "P1Y2M", "PT1H", "P1DT2H", "P", "PT", "1M"]),
)


def _disagreements(name: str, values: Iterable[str]) -> list[str]:
    checker = FacetChecker()
    return [
        value
        for value in values
        if (checker.type_breaks(Facet(type=name), value) is None) != _vendored_accepts(f"{name}Type", value)
    ]


@pytest.mark.parametrize("name", _SDMX_TYPES)
def test_the_checker_reads_every_reporting_edge_as_the_vendored_schema_does(name: str) -> None:
    assert _disagreements(name, _REPORTING_EDGES) == []


@pytest.mark.parametrize("name", _SDMX_TYPES)
@given(value=st.one_of(_PERIOD_TEXT, _RANGE))
def test_the_checker_reads_each_sdmx_type_as_the_vendored_schema_does(name: str, value: str) -> None:
    assert _disagreements(name, [value]) == []


def test_the_comparison_can_disagree() -> None:
    """The discrimination check: two different types disagree on a value."""
    assert _vendored_accepts("ReportingQuarterType", "2020-Q1")
    assert FacetChecker().type_breaks(Facet(type="ReportingMonth"), "2020-Q1") is not None


# ── each facet ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("facet", "value", "broken"),
    [
        (Facet(type=DataType.ReportingQuarter), "2020-Q5", "is not of type ReportingQuarter"),
        (Facet(type=DataType.ObservationalTimePeriod), "2021-02-29", "is not of type ObservationalTimePeriod"),
        (Facet(type="TimeRange"), "2020", "is not of type TimeRange"),
        (Facet(type=DataType.Integer), "1.0", "is not of type Integer"),
        (Facet(type=DataType.Boolean), "yes", "is not of type Boolean"),
        (Facet(min_length=3), "ab", "is 2 characters long, shorter than minLength 3"),
        (Facet(max_length=3), "abcd", "is 4 characters long, longer than maxLength 3"),
        (Facet(type=DataType.Integer, min_value=0), "-1", "is below minValue 0"),
        (Facet(type=DataType.Integer, max_value=10), "11", "is above maxValue 10"),
        (Facet(type=DataType.ExclusiveValueRange, min_value=0), "0", "equals the exclusive minValue 0"),
        (Facet(type=DataType.Double, max_value=1), "NaN", "is NaN, which no bound holds"),
        (
            Facet(type=DataType.Decimal, decimals=2),
            "1.255",
            "has 3 digits after the decimal separator, more than decimals 2",
        ),
        (Facet(pattern="[a-z-[aeiou]]+"), "bad", "does not match the pattern '[a-z-[aeiou]]+'"),
        (Facet(pattern="[A-Z]{2}"), "CAN", "does not match the pattern '[A-Z]{2}'"),
    ],
)
def test_a_value_breaking_one_facet_names_it(facet: Facet, value: str, broken: str) -> None:
    assert FacetChecker().breaks(facet, value) == broken


@pytest.mark.parametrize(
    ("facet", "value"),
    [
        (Facet(type=DataType.ReportingQuarter), "2020-Q4"),
        (Facet(type=DataType.ObservationalTimePeriod), "2020-01-01/P1M"),
        (Facet(type=DataType.Integer, min_value=0, max_value=10), " 10 "),
        (Facet(type=DataType.ExclusiveValueRange, min_value=0), "0.1"),
        (Facet(type=DataType.Decimal, decimals=2), "3"),
        (Facet(type=DataType.Double, max_value=1), "-INF"),
        (Facet(pattern="[a-z-[aeiou]]+"), "bcd"),
        (Facet(pattern=r"\i\c*"), "a1"),
        (Facet(type="KeyValues", max_length=5), "A.B"),
    ],
)
def test_a_value_meeting_every_facet_passes(facet: Facet, value: str) -> None:
    assert FacetChecker().breaks(facet, value) is None


def test_a_pattern_outside_the_xsd_dialect_is_named() -> None:
    broken = FacetChecker().breaks(Facet(pattern="(?=x)x"), "x")
    assert broken is not None
    assert broken.startswith("its pattern '(?=x)x' is not an XML Schema regular expression")


def test_a_sentinel_value_meets_the_type_alone() -> None:
    facet = Facet(
        type=DataType.Integer,
        min_value=0,
        pattern="[0-9]",
        sentinel_values=(SentinelValue(value="-99", name={"en": "not applicable"}),),
    )
    checker = FacetChecker()
    assert checker.breaks(facet, "-99") is None
    assert checker.breaks(facet, "-98") == "is below minValue 0"
    sentinel_of_wrong_type = Facet(
        type=DataType.Integer, sentinel_values=(SentinelValue(value="NA", name={"en": "x"}),)
    )
    assert checker.breaks(sentinel_of_wrong_type, "NA") == "is not of type Integer"


def test_each_type_and_pattern_is_compiled_once_per_checker() -> None:
    checker = FacetChecker()
    facet = Facet(type=DataType.Integer, pattern="[0-9]+")
    for value in ("1", "2", "x", "30"):
        checker.breaks(facet, value)
    assert set(checker._compiled) == {("xs:int", None), ("xs:int", "[0-9]+")}
