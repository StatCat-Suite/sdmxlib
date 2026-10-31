r"""Whether a value meets a text format: a `Facet`'s type, lengths, bounds, decimals and pattern (GH-297).

libxml2, through lxml, checks the type and the pattern as XML Schema defines
them. The type is the XSD type the information model maps a `DataType` to
(IM §3.6.3.3), or one of SDMX's own text types, which ``_facet_types.xsd``
holds as SDMXCommon.xsd defines them. A pattern is read in the XSD regex
dialect it is written in: anchored, with class subtraction and the ``\i`` and
``\c`` escapes. Python checks the rest: the lengths count characters, the
bounds compare numbers, inclusive unless the type is `ExclusiveValueRange`,
and ``decimals`` counts the digits after the decimal separator.

The sequence facets (`is_sequence`, `interval`, `start_value`, `end_value`,
`time_interval`, `start_time`, `end_time`) describe how a series of values
is generated and are not checked. A value equal to one of the facet's
sentinel values is checked against the type alone: a sentinel has its special
meaning "within their data type" (IM §5.3.2.2).
"""

import re
from decimal import Decimal, InvalidOperation
from importlib import resources

from lxml import etree

from sdmxlib.model.representation import Facet

_XS = "http://www.w3.org/2001/XMLSchema"

#: The XSD type of each SDMX text type, by the type's name (IM §3.6.3.3, and
#: SDMXCommon.xsd for SDMX's own). A type missing here, such as XHTML or
#: KeyValues, is checked as ``xs:string``.
_XSD_TYPES: dict[str, str] = {
    "String": "xs:string",
    "Alpha": "AlphaType",
    "AlphaNumeric": "AlphaNumericType",
    "Numeric": "NumericType",
    "BigInteger": "xs:integer",
    "Integer": "xs:int",
    "Long": "xs:long",
    "Short": "xs:short",
    "Decimal": "xs:decimal",
    "Float": "xs:float",
    "Double": "xs:double",
    "Boolean": "xs:boolean",
    "URI": "xs:anyURI",
    "Count": "xs:integer",
    "InclusiveValueRange": "xs:decimal",
    "ExclusiveValueRange": "xs:decimal",
    "Incremental": "xs:decimal",
    "ObservationalTimePeriod": "ObservationalTimePeriodType",
    "StandardTimePeriod": "StandardTimePeriodType",
    "BasicTimePeriod": "BasicTimePeriodType",
    "GregorianTimePeriod": "GregorianTimePeriodType",
    "GregorianYear": "xs:gYear",
    "GregorianYearMonth": "xs:gYearMonth",
    "GregorianDay": "xs:date",
    "ReportingTimePeriod": "ReportingTimePeriodType",
    "ReportingYear": "ReportingYearType",
    "ReportingSemester": "ReportingSemesterType",
    "ReportingTrimester": "ReportingTrimesterType",
    "ReportingQuarter": "ReportingQuarterType",
    "ReportingMonth": "ReportingMonthType",
    "ReportingWeek": "ReportingWeekType",
    "ReportingDay": "ReportingDayType",
    "DateTime": "xs:dateTime",
    "TimeRange": "TimeRangeType",
    "Month": "xs:gMonth",
    "MonthDay": "xs:gMonthDay",
    "Day": "xs:gDay",
    "Time": "xs:time",
    "Duration": "xs:duration",
}

#: The types a value is a number of, so `min_value`, `max_value` and `decimals` apply.
_NUMERIC = frozenset(
    {
        "Numeric",
        "BigInteger",
        "Integer",
        "Long",
        "Short",
        "Decimal",
        "Float",
        "Double",
        "Count",
        "InclusiveValueRange",
        "ExclusiveValueRange",
        "Incremental",
    }
)

#: A number's digits after the decimal separator, before any exponent.
_FRACTION = re.compile(r"\s*[+-]?\d*(?:\.(\d*))?(?:[eE][+-]?\d+)?\s*")


def _type_name(facet: Facet) -> str:
    return "String" if facet.type is None else str(facet.type)


class FacetChecker:
    """Checks values against facets, compiling each type and pattern once per checker.

    One checker serves one validation run; it holds the compiled schemas and
    nothing else, so it is dropped with the run.
    """

    def __init__(self) -> None:
        """A checker with nothing compiled."""
        self._types = etree.fromstring(resources.files("sdmxlib").joinpath("_facet_types.xsd").read_bytes())
        self._compiled: dict[tuple[str, str | None], etree.XMLSchema | str] = {}

    def _schema(self, base: str, pattern: str | None) -> etree.XMLSchema | str:
        """The schema of one element of `base` restricted by `pattern`, or why `pattern` does not compile."""
        key = (base, pattern)
        found = self._compiled.get(key)
        if found is None:
            document = etree.fromstring(etree.tostring(self._types))
            element = etree.SubElement(document, f"{{{_XS}}}element", name="v")
            simple = etree.SubElement(element, f"{{{_XS}}}simpleType")
            restriction = etree.SubElement(simple, f"{{{_XS}}}restriction", base=base)
            if pattern is not None:
                etree.SubElement(restriction, f"{{{_XS}}}pattern", value=pattern)
            try:
                found = etree.XMLSchema(document)
            except etree.XMLSchemaParseError as exc:
                found = f"its pattern {pattern!r} is not an XML Schema regular expression ({exc})"
            self._compiled[key] = found
        return found

    def _meets(self, base: str, pattern: str | None, value: str) -> bool | str:
        """Whether `value` is a `base` matching `pattern`; why not, when the pattern does not compile."""
        schema = self._schema(base, pattern)
        if isinstance(schema, str):
            return schema
        element = etree.Element("v")
        try:
            element.text = value
        except ValueError:
            return False
        return schema.validate(etree.ElementTree(element))

    def type_breaks(self, facet: Facet, value: str) -> str | None:
        """Why `value` is not a value of `facet`'s type, or None when it is."""
        name = _type_name(facet)
        met = self._meets(_XSD_TYPES.get(name, "xs:string"), None, value)
        return None if met is True else f"is not of type {name}"

    def breaks(self, facet: Facet, value: str) -> str | None:
        """Why `value` does not meet `facet`, or None when it does.

        Args:
            facet: The text format.
            value: The value as written.

        Returns:
            The first facet `value` breaks, as a clause: ``is not of type ReportingQuarter``.
        """
        broken = self.type_breaks(facet, value)
        if broken is not None or value in {sentinel.value for sentinel in facet.sentinel_values}:
            return broken
        if facet.min_length is not None and len(value) < facet.min_length:
            return f"is {len(value)} characters long, shorter than minLength {facet.min_length}"
        if facet.max_length is not None and len(value) > facet.max_length:
            return f"is {len(value)} characters long, longer than maxLength {facet.max_length}"
        name = _type_name(facet)
        if name in _NUMERIC:
            broken = _bounds_broken(facet, value, exclusive=name == "ExclusiveValueRange")
            if broken is not None:
                return broken
        if facet.pattern is not None:
            met = self._meets(_XSD_TYPES.get(name, "xs:string"), facet.pattern, value)
            if met is not True:
                return met if isinstance(met, str) else f"does not match the pattern {facet.pattern!r}"
        return None


def _bounds_broken(facet: Facet, value: str, *, exclusive: bool) -> str | None:
    """The numeric facets `value`, a number of the facet's type, breaks: its bounds and ``decimals``."""
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        return None
    if number.is_nan() and (facet.min_value is not None or facet.max_value is not None):
        return "is NaN, which no bound holds"
    if facet.min_value is not None:
        low = Decimal(repr(facet.min_value))
        if number < low or (exclusive and number == low):
            return f"is below minValue {facet.min_value}" if number < low else f"equals the exclusive minValue {low}"
    if facet.max_value is not None:
        high = Decimal(repr(facet.max_value))
        if number > high or (exclusive and number == high):
            return f"is above maxValue {facet.max_value}" if number > high else f"equals the exclusive maxValue {high}"
    if facet.decimals is not None:
        fraction = _FRACTION.fullmatch(value)
        digits = len(fraction.group(1) or "") if fraction is not None else 0
        if digits > facet.decimals:
            return f"has {digits} digits after the decimal separator, more than decimals {facet.decimals}"
    return None
