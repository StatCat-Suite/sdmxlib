"""Representation types — DataType, Facet, Representation alias."""

from enum import StrEnum

from attrs import evolve, field, frozen

from sdmxlib.model._rules import positive_integer
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.istring import to_istring as _to_istring
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueList


class DataType(StrEnum):
    """SDMX data types for non-enumerated representations.

    Values match the SDMX-ML ``textType`` attribute strings exactly, so
    ``DataType.String == "String"`` and existing string-passing code continues
    to work unchanged.

    Example:
        Facet(type=DataType.String, max_length=100)
        Facet(type=DataType.ObservationalTimePeriod)
        Facet(type="String")  # str also accepted
    """

    String = "String"
    Alpha = "Alpha"
    AlphaNumeric = "AlphaNumeric"
    Numeric = "Numeric"
    BigInteger = "BigInteger"
    Integer = "Integer"
    Long = "Long"
    Short = "Short"
    Decimal = "Decimal"
    Float = "Float"
    Double = "Double"
    Boolean = "Boolean"
    # NOTE: `URL`, `ReportingHour`, `Year` and `Attachment` were removed in
    # #224. None appears in the SDMX-ML 2.1 / 3.0 / 3.1 DataType enumerations,
    # so a Facet carrying one serialised to XML that failed validation in every
    # version. Each sat beside its valid counterpart — URI, GregorianYear,
    # ReportingYear/Day — and none was used anywhere in the codebase.
    URI = "URI"
    Count = "Count"
    InclusiveValueRange = "InclusiveValueRange"
    ExclusiveValueRange = "ExclusiveValueRange"
    Incremental = "Incremental"
    ObservationalTimePeriod = "ObservationalTimePeriod"
    StandardTimePeriod = "StandardTimePeriod"
    BasicTimePeriod = "BasicTimePeriod"
    GregorianTimePeriod = "GregorianTimePeriod"
    GregorianYear = "GregorianYear"
    GregorianYearMonth = "GregorianYearMonth"
    GregorianDay = "GregorianDay"
    ReportingTimePeriod = "ReportingTimePeriod"
    ReportingYear = "ReportingYear"
    ReportingSemester = "ReportingSemester"
    ReportingTrimester = "ReportingTrimester"
    ReportingQuarter = "ReportingQuarter"
    ReportingMonth = "ReportingMonth"
    ReportingWeek = "ReportingWeek"
    ReportingDay = "ReportingDay"
    DateTime = "DateTime"
    Time = "Time"
    Duration = "Duration"
    Month = "Month"
    MonthDay = "MonthDay"
    Day = "Day"
    XHTML = "XHTML"


@frozen
class SentinelValue:
    """A value with a special meaning in a text format: ``-1`` for "not applicable" (#422).

    SDMX 3.x's ``SentinelValueType``: the value as written — any simple type,
    held as its text, since SDMX-JSON may send a number — and its required
    multilingual name and optional description.
    """

    value: str
    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)


@frozen
class Facet:
    """Non-enumerated representation with optional constraints.

    ``type`` accepts a `DataType` member or a raw string (for
    non-standard values from registry responses).

    Example:
        Facet(type=DataType.String, max_length=100)
        Facet(type=DataType.ObservationalTimePeriod)
        Facet(type="String")  # str accepted; DataType.String preferred
    """

    type: DataType | str | None = None
    max_length: int | None = field(default=None, validator=positive_integer)
    min_length: int | None = field(default=None, validator=positive_integer)
    max_value: float | None = None
    min_value: float | None = None
    decimals: int | None = field(default=None, validator=positive_integer)
    pattern: str | None = None
    start_value: float | None = None
    end_value: float | None = None
    is_sequence: bool | None = None
    #: Whether a text value may come in several languages. False unless stated:
    #: a reader fills in its format's default where that format has one, `true`
    #: in SDMX-ML 2.1 and 3.0 and `false` in 3.1 (#503).
    is_multilingual: bool = False
    #: A sequence's step, as `start_value` / `end_value` bound it (#422).
    interval: float | None = None
    #: A time sequence's step, an ``xs:duration`` (``P1D``), and its bounds,
    #: SDMX standard time periods kept as written (#422). No level coding
    #: format takes these.
    time_interval: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    #: Values with a special meaning in this format (SDMX 3.x, #422) — only on
    #: a representation's text format: neither an enumeration format nor a
    #: level's coding format may carry one, and 2.1 has none at all.
    sentinel_values: tuple[SentinelValue, ...] = field(factory=tuple, converter=tuple)


#: What a component's values are enumerated by: SDMX 3.x allows a `ValueList`
#: wherever it allows a `Codelist` (``AnyCodelistReferenceType``, #422).
type Enumeration = Codelist | ValueList

# Type alias — a component's representation is a reference to its enumeration
# (a codelist or a value list), or a facet (free text with optional
# constraints), or absent.
type Representation = Ref[Enumeration] | Facet | None


def to_representation(
    v: Ref[Enumeration] | SdmxUrn[Codelist] | SdmxUrn[ValueList] | Codelist | ValueList | Facet | None,
) -> Representation:
    """Attrs converter for a component's representation.

    A URN or an artefact becomes a `Ref` to it; a `Ref`, a `Facet` or None
    passes through. Which enumeration a URN names is its class's to say — a
    ``codelist.ValueList=`` URN is a value list — so nothing is checked here.
    """
    if isinstance(v, SdmxUrn):
        return Ref[Enumeration].unresolved(v)
    if isinstance(v, Codelist | ValueList):
        return Ref.to(v)
    return v


def to_text_representation(
    v: Ref[Enumeration] | SdmxUrn[Codelist] | SdmxUrn[ValueList] | Codelist | ValueList | Facet | None,
) -> Representation:
    """`to_representation` for a component or concept, whose text format's type is `String` unless stated (#503).

    Every format gives a component's or concept's ``textType`` that default,
    so an unstated one is held as what it means, and a facet built in code
    compares equal to the same facet read back, its type written or not.
    """
    representation = to_representation(v)
    if isinstance(representation, Facet):
        return to_text_format(representation)
    return representation


def to_text_format(v: Facet | None) -> Facet | None:
    """A text format whose type is `String` unless stated: a component's, a concept's or a map target's (#503)."""
    return v if v is None or v.type is not None else evolve(v, type=DataType.String)


def to_time_format(v: Facet | None) -> Facet:
    """A time dimension's format, whose type is `ObservationalTimePeriod` unless stated (#503).

    Never absent: every SDMX format requires a time dimension's format — the
    XSDs' `TimeDimensionType` has a mandatory `LocalRepresentation`, and
    SDMX-JSON a mandatory `localRepresentation.format` — and gives its type a
    default. So none stated *is* that default, held as a value (#507), not a
    `None` every writer turned into a document its schema rejects.
    """
    if v is None:
        return Facet(type=DataType.ObservationalTimePeriod)
    return v if v.type is not None else evolve(v, type=DataType.ObservationalTimePeriod)


def enumeration_ref(urn_text: str) -> Ref[Codelist] | Ref[ValueList]:
    """An unresolved reference to the enumeration `urn_text` names, typed by its class.

    For a reader rebuilding a representation from a stored or written URN: a
    ``codelist.ValueList=`` URN is a `ValueList`, any other a `Codelist` — the
    class check of a wrong one is the reader's, as it always was.
    """
    if urn_text.split("=", 1)[0].endswith(".ValueList"):
        return Ref[ValueList].unresolved(ValueList.parse_urn(urn_text))
    return Ref[Codelist].unresolved(Codelist.parse_urn(urn_text))
