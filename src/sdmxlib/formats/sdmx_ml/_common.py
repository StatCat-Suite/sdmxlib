"""Reader and writer helpers shared by SDMX-ML 2.1, 3.0 and 3.1.

The versions differ only in namespace URIs and a few additional features
(3.0 supports URN-text refs as an alternative to ``<Ref>`` children, etc.).
`ReaderHelpers` and `WriterHelpers` are each constructed once per version
module with that version's namespace triple.

The 3.0 superset behaviour is used everywhere — it's strictly broader
than 2.1, so 2.1 messages parse correctly (the extra branches simply
don't trigger).
"""

from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, NamedTuple

import attrs
from lxml import etree
from typing_extensions import TypedDict

from sdmxlib.formats._coerce import to_datetime, to_enum, to_float, to_int
from sdmxlib.formats._refuse import build_each_xml, refuse_xml
from sdmxlib.formats._tags import XML_LANG, optional_xml_tag, xml_texts
from sdmxlib.formats.sdmx_ml._closed_world import BY_STRUCTURE_NS, check_artefact, required
from sdmxlib.model._container import inner_id, member_id
from sdmxlib.model.annotations import Annotation, Annotations, AnnotationURL
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    GeographicCodelist,
    GeoGridCodelist,
    InclusionMode,
    MemberValue,
    code_of_kind,
    codelist_of_kind,
)
from sdmxlib.model.collections import ItemList, SupportsId, SupportsUri
from sdmxlib.model.concept import Concept, ConceptScheme, ISOConceptReference
from sdmxlib.model.constraint import (
    AvailabilityConstraint,
    ComponentValueSet,
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    DataKey,
    DataKeySet,
    DataSetReference,
    KeyValue,
    QueryableDataSource,
    ReferencePeriod,
    RegionValue,
    ReleaseCalendar,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import Refusal
from sdmxlib.model.hierarchy import Level
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link
from sdmxlib.model.mapping import ItemMap, SchemeMap
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import Agency
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, SupportsUrn
from sdmxlib.model.representation import Facet, Representation, SentinelValue
from sdmxlib.model.urn import ParsesUrn, SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError, check_writable
from sdmxlib.model.valuelist import ValueItem, ValueList
from sdmxlib.model.version import is_final

#: Why a 2.1 constraint on metadata is refused (`ReaderHelpers.constrains_metadata`).
METADATA_CONSTRAINT = (
    "a metadata constraint — a 2.1 constraint attached to metadata or holding a metadata key set or region — "
    "which sdmxlib does not model, as it does not model 3.x's MetadataConstraint"
)

# A shared empty value-type singleton used by reader hot paths to avoid
# constructing a fresh attrs instance for the (very common) no-data case.
# Safe because the class is immutable from the user's perspective.
_EMPTY_ANNOTATIONS = Annotations()


class SectionEntry(NamedTuple):
    """One (message attribute, per-item writer) pair for a writer's section table.

    Storage erases the artefact type to `Any`, but entries are built through
    `section_entry`, which binds the two sides to one type variable — so the
    erasure happens *after* the pairing has been checked, at the one place the
    mistake is actually made. See #210.
    """

    # Erased per-entry type parameter — see the sibling in sdmx_json/writer.py.
    # One section also passes the *message* itself (SDMX-ML 2.1 wraps
    # hierarchies in a single container), so no artefact bound would fit.
    get: Callable[[StructureMessage], Sequence[Any]]
    write: Callable[[etree._Element, Any], None]


def section_entry[T](
    get: Callable[[StructureMessage], Sequence[T]],
    write: Callable[[etree._Element, T], None],
) -> SectionEntry:
    """Pair a message accessor with the writer for the artefacts it yields.

    ``T`` is inferred from *both* arguments, so a getter and writer that
    disagree are a type error rather than an ``AttributeError`` deep inside
    ``write()``. The getter also replaces a ``getattr(msg, "name")`` lookup,
    which no annotation was ever checking.

    Args:
        get: Reads one artefact collection off the message.
        write: Appends one of those artefacts to a parent element.

    Returns:
        The type-erased entry for a section table.
    """
    return SectionEntry(get, write)


def xsd_num(value: float | None) -> str | None:
    """Render a numeric facet bound as an XSD attribute value, passing None through.

    Formats via `Decimal` rather than ``str`` because the XSD types these
    bounds ``xs:decimal``, which forbids exponent notation — and Python's
    ``str`` switches to it below about 1e-4 and above 1e16. ``str(3.36e-33)``
    produced ``"3.359122329597789e-33"``, which every SDMX-ML version rejects
    (#224). ``Decimal(str(v))`` round-trips the float's repr exactly, so
    ordinary values are unchanged.

    Args:
        value: The bound, or None.

    An integral value is rendered without a fractional part. That is not
    cosmetic: ``EnumerationFormat`` restricts ``startValue`` / ``endValue`` to
    ``xs:integer`` where ``TextFormat`` allows ``xs:decimal``, so ``0.0``
    is rejected there and ``0`` is accepted by both (#243).

    Returns:
        A decimal literal without an exponent, or None.
    """
    if value is None:
        return None
    number = Decimal(str(value))
    return format(number.to_integral_value(), "f") if number == number.to_integral_value() else format(number, "f")


#: Attributes an element's own XSD type forbids, though `Facet` can carry them.
#:
#: `EnumerationFormat` is typed `CodedTextFormatType`, a *restriction* of
#: `SimpleComponentTextFormatType` that marks `decimals` prohibited and drops
#: `isMultiLingual` from the attribute list entirely — both because it
#: constrains *codes*, which are strings. `TextFormat` on the same table keeps
#: them, which is why this is per-tag rather than per-field.
#:
#: Emitting them produced a document every conformant parser rejects, in all
#: three SDMX-ML versions and in SDMX-JSON, and no fixture had ever put a
#: `decimals` on an enumerated component (#364). `gaps.py` declares the loss so
#: a caller can ask before writing.
_PROHIBITED_ON: dict[str, tuple[str, ...]] = {
    "EnumerationFormat": ("decimals", "isMultiLingual"),
}


#: How a text format's ``isMultiLingual`` applies, by the type its context
#: gives it (#503): a basic component's (`BasicComponentTextFormatType`) may
#: be multilingual, defaulting as `MULTILINGUAL_DEFAULT` says; every other
#: text format prohibits it.
type TextKind = Literal["basic", "other"]

#: The element that owns a ``TextFormat`` → whether its type is the basic
#: one. SDMX-ML 2.1 gives a data structure's components the simple type and
#: 3.x an attribute and a measure the basic one.
_BASIC: dict[str, frozenset[str]] = {
    "2.1": frozenset({"Concept", "MetadataAttribute"}),
    "3.x": frozenset({"Concept", "MetadataAttribute", "Attribute", "Measure"}),
}

#: What ``TextFormatType`` defaults ``isMultiLingual`` to: SDMX 3.1 flipped it (#503).
MULTILINGUAL_DEFAULT: dict[str, bool] = {"2.1": True, "3.0": True, "3.1": False}

#: The element that owns a ``TextFormat`` → the ``textType`` its type defaults
#: or fixes (#503). A reader fills it in when absent, and a writer leaves it out.
_TEXT_TYPE: dict[str, dict[str, str]] = {
    "2.1": {
        "Concept": "String",
        "MetadataAttribute": "String",
        "Dimension": "String",
        "Attribute": "String",
        "PrimaryMeasure": "String",
        "TimeDimension": "ObservationalTimePeriod",
        "ReportPeriodTarget": "ObservationalTimePeriod",
        "ReportingYearStartDay": "MonthDay",
        "KeyDescriptorValuesTarget": "KeyValues",
        "DataSetTarget": "DataSetReference",
        "ConstraintContentTarget": "AttachmentConstraintReference",
        "IdentifiableObjectTarget": "IdentifiableReference",
    },
    "3.x": {
        "Concept": "String",
        "MetadataAttribute": "String",
        "Attribute": "String",
        "Measure": "String",
        "Dimension": "String",
        "TimeDimension": "ObservationalTimePeriod",
    },
}


def sdmx_ml_version(el: etree._Element) -> str:
    """The SDMX-ML version an element's namespace belongs to: ``2.1``, ``3.0`` or ``3.1``."""
    namespace = etree.QName(el).namespace or ""
    return next((version for version, mark in (("2.1", "v2_1"), ("3.1", "v3_1")) if mark in namespace), "3.0")


def _owner(tag: str, holder: etree._Element) -> str:
    """The name of the element a ``TextFormat`` under `holder` describes; a map target's is ``ToTextFormat``."""
    if tag == "ToTextFormat":
        return "ToTextFormat"
    if tag != "TextFormat":
        return ""
    owner = holder.getparent()
    return etree.QName(owner).localname if owner is not None else ""


def text_kind(version: str, tag: str, holder: etree._Element) -> TextKind:
    """Whether the text format `tag` under `holder` is a basic component's, which may be multilingual (#503).

    Read off the tree, so the reader and the writer, which is building the
    same tree, agree without being told. A 2.1 ``ToTextFormat`` is typed
    `TextFormatType`, whose defaults are the basic ones.
    """
    owner = _owner(tag, holder)
    basic = owner == "ToTextFormat" or owner in _BASIC["2.1" if version == "2.1" else "3.x"]
    return "basic" if basic else "other"


def text_type_default(version: str, tag: str, holder: etree._Element) -> str | None:
    """The ``textType`` the text format `tag` under `holder` defaults or fixes; None if it has none (#503)."""
    owner = _owner(tag, holder)
    if owner == "ToTextFormat":
        return "String"
    return _TEXT_TYPE["2.1" if version == "2.1" else "3.x"].get(owner)


def core_enumeration_format(concept: Concept) -> Facet | None:
    """A concept's `enumeration_format`, where its core representation can carry one.

    ``ConceptRepresentation`` puts ``EnumerationFormat`` beside an
    ``Enumeration`` only; a text format leaves nowhere to write it (#422).
    """
    return concept.enumeration_format if isinstance(concept.core_representation, Ref) else None


def coding_format_attrs(facet: Facet) -> dict[str, str]:
    """A level's coding format as the attributes `CodingTextFormatType` permits.

    A restriction of the ordinary text format: `decimals`, `isMultiLingual` and
    the three time facets are prohibited, and `gaps.py` reports them. The
    numeric bounds go through `xsd_num`, because the type makes them
    ``xs:positiveInteger`` and ``str(1.0)`` is ``"1.0"``, which it rejects
    (#243, #364). Shared by the 2.1 and 3.x writers, which each had a copy.
    """
    attrs_out: dict[str, str | None] = {
        "textType": str(facet.type) if facet.type is not None else None,
        "isSequence": _xsd_bool(value=facet.is_sequence),
        "minLength": xsd_num(facet.min_length),
        "maxLength": xsd_num(facet.max_length),
        "startValue": xsd_num(facet.start_value),
        "endValue": xsd_num(facet.end_value),
        "minValue": xsd_num(facet.min_value),
        "maxValue": xsd_num(facet.max_value),
        "pattern": facet.pattern,
        "interval": xsd_num(facet.interval),
    }
    return {name: value for name, value in attrs_out.items() if value is not None}


def _xsd_bool(*, value: bool | None) -> str | None:
    """Render a boolean facet flag as XSD ``true``/``false``, passing None through.

    Keyword-only because FBT001 rejects a boolean positional parameter, and the
    rule is worth keeping on rather than suppressing for one formatter.
    """
    return None if value is None else ("true" if value else "false")


def _require_urn_class(urn: SdmxUrn[object], cls: type, tag: str) -> None:
    """Fail when a URN's artefact class contradicts the slot it appears in.

    ``cls is object`` is the documented spelling for a slot that genuinely
    accepts any artefact (a categorisation source, a metadata target), so the
    check is skipped there rather than being defeated by a coincidence.

    Compares against ``cls.__name__`` because `SdmxUrn.artefact_class` carries
    the SDMX class name, which the model's class names mirror exactly.
    """
    if cls is object or urn.artefact_class == cls.__name__:
        return
    raise SdmxConformanceError(
        tag,
        f"schema requires a {cls.__name__} reference here, but the URN names "
        f"a {urn.artefact_class} ({urn}). The message is not schema-valid.",
    )


def _write_time_range(
    range_el: etree._Element, time_range: TimeRange, qualify: Callable[[str], str], *, v3: bool
) -> None:
    """A `TimeRange`'s bounds — a start and an end, or one of after / before — and its 3.x validity."""
    if v3:
        _set_validity(range_el, time_range.valid_from, time_range.valid_to)
    start, end = time_range.start, time_range.end
    bounds: list[tuple[str, TimeBound]] = []
    if start is not None and end is not None:
        bounds = [("StartPeriod", start), ("EndPeriod", end)]
    elif start is not None:
        bounds = [("AfterPeriod", start)]
    elif end is not None:
        bounds = [("BeforePeriod", end)]
    for name, bound in bounds:
        bound_el = etree.SubElement(range_el, qualify(name))
        if not bound.inclusive:
            bound_el.set("isInclusive", "false")
        bound_el.text = bound.period


class ReferenceFields(TypedDict):
    """`ReferenceAttrs`' keys, open, for a shape that adds to them (PEP 728).

    A closed TypedDict cannot gain keys, so what extends these extends this.
    """

    is_external_reference: bool
    service_url: str | None
    structure_url: str | None
    is_partial_language: bool


class ReferenceAttrs(ReferenceFields, closed=True):
    """The external-reference keywords every maintainable's constructor takes (#422).

    Closed (PEP 728): unpacked into constructors, and only a closed TypedDict
    is known to hold no other key.
    """


def component_id(el: etree._Element, concept: Ref[Concept] | None) -> str:
    """A component's ``@id``, or its concept's: the schema's default when it is omitted (#477).

    ``ComponentBaseType`` makes the id optional in 2.1, 3.0 and 3.1: "if this
    identifier is not supplied, then it is assumed to be the same as the
    identifier of the concept referenced from the concept identity". Read as
    ``""``, a conformant component was refused for an empty id, or kept with one.

    Raises:
        SdmxConformanceError: If the component states neither an id nor a concept to take one from.
    """
    stated = el.get("id")
    if stated is not None:
        return stated
    if concept is not None and concept.urn.item_id:
        return concept.urn.item_id
    element = etree.QName(el).localname
    detail = "a component without @id takes its concept's id, and this one has no ConceptIdentity"
    raise SdmxConformanceError(element, detail)


class ReaderHelpers:
    """Namespace-bound SDMX-ML reader helpers.

    A class rather than a closure factory. The factory form scored 88 on C901
    with 210 statements, and that score was earned rather than an artifact: it
    erased signatures behind ``Callable[...]`` fields and needed three Protocol
    classes — `_ResolveRef`, `_ResolveRefText`, `_ReadRepresentation` — to
    express generic members a method gets for free. Those are now gone. See #209.
    """

    def __init__(self, *, com: str, str_: str, has_is_final: bool, has_links: bool) -> None:
        """Bind the common and structure namespace URIs and pre-build the hot tags.

        `has_is_final` says whether this SDMX-ML version puts `isFinal` on a
        maintainable: 2.1 does, 3.0 and 3.1 do not. `has_links` says whether it
        has `<com:Link>`: 3.0 and 3.1 do, 2.1 does not.
        """
        self.has_is_final = has_is_final
        self.has_links = has_links
        # Clark tags compared once per item, built once per reader.
        self._link = f"{{{com}}}Link"
        self._code = f"{{{str_}}}Code"
        self._geo_feature_set_code = f"{{{str_}}}GeoFeatureSetCode"
        self._geo_grid_code = f"{{{str_}}}GeoGridCode"
        #: Prefix map for every ``find``/``findall``. lxml resolves prefixes per
        #: call — there is no way to bind one to a tree — so each lookup passes it.
        self._ns = {"com": com, "str": str_}

    def read_reference_attrs(self, el: etree._Element) -> ReferenceAttrs:
        """A maintainable's ``isExternalReference``, ``serviceURL`` and ``structureURL`` (#422)."""
        return {
            "is_external_reference": flag(el, "isExternalReference"),
            "service_url": el.get("serviceURL"),
            "structure_url": el.get("structureURL"),
            # 3.1's alone; absent, and so false, everywhere else.
            "is_partial_language": flag(el, "isPartialLanguage"),
        }

    def read_is_partial(self, el: etree._Element) -> bool:
        """An item scheme's ``isPartial`` (#422)."""
        return flag(el, "isPartial")

    def read_legacy_is_final(self, el: etree._Element) -> bool | None:
        """2.1's `@isFinal` where it disagrees with the version, else None (#424).

        Finality is derived from the version (`MaintainableArtefact.is_final`),
        so the attribute is only information when it says something else — a
        `1.0` declared final, or a `1.0.0` declared not. Kept as None when the
        two agree so that the same artefact read from 2.1 and from 3.0 is equal.

        An absent attribute is `false`, the 2.1 XSD's default. A 3.x document
        has no `isFinal` at all, so it is not read there.

        Every maintainable that 2.1 gives an `isFinal` needs this. Six read it
        and eleven did not, so a `isFinal="true"` codelist round-tripped and an
        otherwise identical concept scheme silently came back false (#357).
        """
        if not self.has_is_final:
            return None
        stated = flag(el, "isFinal")
        return None if stated == is_final(el.get("version", "1.0")) else stated

    def read_istring(self, el: etree._Element, child_tag: str = "Name", *, prefix: str = "com") -> InternationalString:
        """Read com:Name (or com:Description) children as InternationalString.

        A contact's ``str:Department`` and ``str:Role`` are the same `TextType`
        in the structure namespace, hence `prefix`.

        An empty element is read as the empty text it is, never skipped: a
        blank name is the validator's to refuse, as it is when SDMX-JSON
        spells it ``""``, rather than vanishing in one format and not the
        other (#452).
        """
        return xml_texts(el.findall(f"{prefix}:{child_tag}", namespaces=self._ns), child_tag)

    @staticmethod
    def _multilingual(tf_el: etree._Element) -> bool:
        stated = optional_flag(tf_el, "isMultiLingual")
        if stated is not None:
            return stated
        holder = tf_el.getparent()
        version = sdmx_ml_version(tf_el)
        kind = "other" if holder is None else text_kind(version, etree.QName(tf_el).localname, holder)
        return MULTILINGUAL_DEFAULT[version] if kind == "basic" else False

    @staticmethod
    def _text_type_default(tf_el: etree._Element) -> str | None:
        holder = tf_el.getparent()
        if holder is None:
            return None
        return text_type_default(sdmx_ml_version(tf_el), etree.QName(tf_el).localname, holder)

    def read_occurs(self, rep_el: etree._Element | None) -> tuple[int, int | None]:
        """A representation's `minOccurs` / `maxOccurs` (#422), 1 when absent; None is unbounded."""
        if rep_el is None:
            return 1, 1
        max_text = rep_el.get("maxOccurs")
        max_occurs = None if max_text == "unbounded" else self.read_int(rep_el, "maxOccurs", default=1)
        return self.read_int(rep_el, "minOccurs", default=1), max_occurs

    def read_child_uri(self, el: etree._Element | None, child_tag: str) -> str | None:
        """The ``@uri`` of `el`'s ``str:`` child `child_tag` — a component list's (#422)."""
        child = None if el is None else el.find(f"str:{child_tag}", namespaces=self._ns)
        return None if child is None else child.get("uri")

    def read_links(self, el: etree._Element) -> tuple[Link, ...]:
        """`el`'s ``<com:Link>`` children, in order — SDMX 3.x's; a 2.1 element has none (#422)."""
        if not self.has_links:
            return ()
        return tuple(
            Link(
                rel=required(link_el, "rel"),
                url=required(link_el, "url"),
                urn=link_el.get("urn"),
                type=link_el.get("type"),
            )
            for link_el in el.iterchildren(self._link)
        )

    def read_child_links(self, el: etree._Element | None, child_tag: str) -> tuple[Link, ...]:
        """The links of `el`'s ``str:`` child `child_tag` — a component list's (#422)."""
        child = None if el is None else el.find(f"str:{child_tag}", namespaces=self._ns)
        return () if child is None else self.read_links(child)

    def read_child_annotations(self, el: etree._Element | None, child_tag: str) -> Annotations:
        """The annotations of `el`'s ``str:`` child `child_tag` — a component list, say — if it has one."""
        child = None if el is None else el.find(f"str:{child_tag}", namespaces=self._ns)
        return _EMPTY_ANNOTATIONS if child is None else self.read_annotations(child)

    def read_annotations(self, el: etree._Element) -> Annotations:
        """Read com:Annotations block."""
        anns_el = el.find("com:Annotations", namespaces=self._ns)
        if anns_el is None:
            return _EMPTY_ANNOTATIONS
        items: list[Annotation] = []
        for ann_el in anns_el.findall("com:Annotation", namespaces=self._ns):
            title_el = ann_el.find("com:AnnotationTitle", namespaces=self._ns)
            type_el = ann_el.find("com:AnnotationType", namespaces=self._ns)
            # Every non-empty one, with its language (#422). Empty elements carry
            # nothing — Eurostat ships ~830,000 of them — and are not a URL.
            urls = [
                AnnotationURL(url_el.text, optional_xml_tag(url_el, "AnnotationURL"))
                for url_el in ann_el.findall("com:AnnotationURL", namespaces=self._ns)
                if url_el.text and url_el.text.strip()
            ]
            # 3.0 added AnnotationValue; a 2.1 document has no such element, so
            # `find` returns None and this needs no version branch (#368).
            value_el = ann_el.find("com:AnnotationValue", namespaces=self._ns)
            text_els = [t for t in ann_el.findall("com:AnnotationText", namespaces=self._ns) if t.text]
            items.append(
                Annotation(
                    id=ann_el.get("id"),
                    title=title_el.text if title_el is not None else None,
                    type=type_el.text if type_el is not None else None,
                    urls=urls,
                    text=xml_texts(text_els, "AnnotationText"),
                    value=value_el.text if value_el is not None else None,
                )
            )
        return Annotations(items)

    @staticmethod
    def read_int(el: etree._Element, attr: str, *, default: int) -> int:
        """Read an integer attribute, or fail with the declared reader error.

        ``int(el.get(...))`` on document text raises a bare ``ValueError`` for
        anything that is not a numeral, which escaped `parse` as itself — an
        empty ``minOccurs=""`` was enough. The value comes from the payload, so
        a bad one means the payload cannot be read (#247).
        """
        raw = el.get(attr)
        if raw is None:
            return default
        return to_int(raw, what=f"<{etree.QName(el).localname}> @{attr}")

    def read_datetime_attr(self, el: etree._Element, attr: str) -> datetime | None:
        """Parse an optional xs:dateTime attribute (e.g. validFrom, validTo)."""
        val = el.get(attr)
        if not val:
            return None
        return to_datetime(val, what=f"@{attr}")

    def resolve_agency(self, agency_id: str, agencies: dict[str, Agency]) -> Agency:
        """Return the rich Agency if parsed from an AgencyScheme, else a minimal one."""
        return agencies.get(agency_id, Agency(id=agency_id))

    def ref_el_to_urn(self, ref_el: etree._Element, expected: type | None = None) -> SdmxUrn[Any] | None:
        # `Any`, not `object`: the result feeds `Ref[T].unresolved` in
        # `read_representation`, which recovers the parameter. See #286.
        """Convert a Ref element to a SdmxUrn.

        Handles maintainable refs and item refs that carry
        ``maintainableParentID``. A ``<Ref>`` has no ``@urn`` in any version,
        and the closed-world check refuses one that does before this runs.

        Args:
            ref_el: The ``<Ref>`` element.
            expected: The artefact class the containing field expects, used to
                supply ``@package`` / ``@class`` when the wire omits them —
                see below. `None`, or a class carrying neither, disables the
                fallback.

        **``@package`` and ``@class`` are optional, and omitting them is not a
        malformed document** (#240). Across the 2.1 schemas `package` is
        optional on 97 ref types and required on 10, and 83 of them declare a
        ``fixed=`` value — XSD's way of saying "you may leave this out, the
        schema supplies it". This required both and returned `None` without
        them, so the caller stored `None` and the reference vanished: no
        exception, no warning, and a document that validates.

        `expected` is that ``fixed=``, in Python. It is the same witness
        `resolve_ref` already uses as a conformance guard, so supplying the
        value it would have checked against costs nothing and cannot
        contradict it.

        A genuinely heterogeneous ref — a categorisation source, a metadata
        target — passes `object`, which carries no pair and so keeps the old
        behaviour. That is right: those must self-describe, because nothing
        else knows what they point at.
        """
        # Required on every 2.1 maintainable and item ref type; read as "",
        # the reference was silently dropped (#474).
        agency = required(ref_el, "agencyID")
        id_ = required(ref_el, "id")
        # Every concrete 2.1 ref type defaults both versions to 1.0, so an
        # omitted one names 1.0, never whatever is latest (#476).
        version = ref_el.get("version", "1.0")
        package = ref_el.get("package") or getattr(expected, "sdmx_package", "") or ""
        cls = ref_el.get("class") or getattr(expected, "sdmx_class", "") or ""
        if not (agency and id_ and package and cls):
            return None
        parent_id = ref_el.get("maintainableParentID")
        parent_version = ref_el.get("maintainableParentVersion", "1.0")
        if parent_id and cls == "HierarchicalCode" and (container := ref_el.get("containerID")):
            # 2.1 nests a Hierarchy inside a HierarchicalCodelist, and its
            # `HierarchicalCodeRefType` names both: the HCL as the parent, the
            # Hierarchy as `containerID` (required there). The model follows
            # 3.0, where the Hierarchy is the maintainable and owns the node —
            # so the parent is the hierarchy, with the HCL's agency and version
            # and the id `member_id` gives it, exactly as `_read_hierarchies`
            # builds it (#439, #421).
            parent_id = member_id(container, parent_id)
        if parent_id:
            # Item URN: id_ is the item, parent_id is the maintainable container
            return SdmxUrn.make(
                object,
                package=package,
                artefact_class=cls,
                agency=agency,
                id=parent_id,
                version=parent_version,
                item_id=id_,
            )
        return SdmxUrn.make(
            object,
            package=package,
            artefact_class=cls,
            agency=agency,
            id=id_,
            version=version,
        )

    def resolve_ref[T](self, container_el: etree._Element, cls: type[T]) -> Ref[T] | None:
        """Find a reference and resolve it against the index.

        *cls* is a type witness *and* a conformance guard: the caller supplies
        the artefact class the containing field expects (``Concept``,
        ``Codelist``, ``DataStructure``, …), so the returned ``Ref[T]`` carries
        a real type rather than ``Ref[object]``. The parsed URN is
        ``SdmxUrn[object]``, as ``SdmxUrn.parse`` returns it (#399); callers
        only see ``Ref[T]``. Genuinely heterogeneous refs (a categorisation
        source, a metadata target) pass ``object``, which disables the guard.
        See ``docs/design/phantom-generics.md``.

        A URN whose artefact class contradicts *cls* raises
        `SdmxConformanceError`: no schema-valid message can produce it, and
        reifying it would put an artefact of one class into a slot typed for
        another. That is separate from a reference the index simply does not
        contain — a *dangling* ref is normal for a partial message and returns
        an unresolved `Ref`, not an error.

        Two ref styles are supported: a child ``<Ref>``, and a URN — the
        element's own text in 3.x, a child ``<URN>`` in 2.1 (`urn_text`).
        """

        def _checked(urn: SdmxUrn[Any], tag: str) -> Ref[T]:
            _require_urn_class(urn, cls, tag)
            # Bound to the document's index, which the reader names (#492): it
            # finds what the URN names on access, wherever the document defines it.
            return Ref.unresolved(urn)

        tag = etree.QName(container_el).localname
        # Style 1: child Ref element
        ref_el = container_el.find("Ref")
        if ref_el is not None:
            urn = self.ref_el_to_urn(ref_el, cls)
            if urn is not None:
                return _checked(urn, tag)
        # Style 2: a URN
        text = self.urn_text(container_el)
        if text.startswith("urn:"):
            return _checked(SdmxUrn.parse(text), tag)
        return None

    def urn_text(self, container_el: etree._Element) -> str:
        """The URN a reference element carries as text, or ``""``.

        3.x writes it as the element's own text. 2.1's `ReferenceType` is a
        choice of ``<Ref>``, ``<Ref>`` then ``<URN>``, or ``<URN>`` alone —
        and the reader looked only for ``<Ref>``, so a schema-valid document
        using the last lost every reference in it with nothing reported (#422).
        """
        urn_el = container_el.find("URN")
        holder = container_el if urn_el is None else urn_el
        return (holder.text or "").strip()

    def resolve_ref_text[T](self, el: etree._Element | None, cls: type[T]) -> Ref[T] | None:
        """Resolve an element whose body is a URN-text reference.

        None-tolerant sibling of `resolve_ref`, for the SDMX 3.0 fields whose
        container element is optional — callers pass ``el.find(...)`` straight
        in. Delegates rather than reimplementing, so the ``isinstance`` guard
        and the ``Ref[T]`` typing are shared: ``resolve_ref``'s Style 2 branch
        is exactly this lookup.

        The ml30 and ml31 readers each carried a private copy of this that
        returned ``Any``, took no type witness, and skipped the isinstance
        guard — so a URN pointing at a wrong-typed artefact produced a
        mistyped *resolved* Ref instead of an unresolved one.
        """
        if el is None:
            return None
        return self.resolve_ref(el, cls)

    def index_artefact(self, index: dict[str, object], obj: SupportsUrn, artefact_type: type[SupportsUrn]) -> None:
        """Add a maintainable artefact to the resolution index under its URN.

        Under its versioned URN alone. A version-less "latest" key too would
        bind a 2.1 ``<URN>`` that omits the version to whichever version the
        message holds, where #476 says an omitted version never means latest;
        such a reference stays unresolved.
        """
        package = artefact_type.sdmx_package
        cls = artefact_type.sdmx_class
        urn = SdmxUrn.make(
            artefact_type,
            package=package,
            artefact_class=cls,
            agency=obj.agency_id,
            id=obj.id,
            version=obj.version,
        )
        index[str(urn)] = obj

    def index_item(
        self,
        index: dict[str, object],
        item: SupportsId,
        parent: SupportsUrn,
        package: str,
        cls: str,
    ) -> None:
        """Add an item (e.g. Concept) to the index under its item URN."""
        urn = SdmxUrn.make(
            object,
            package=package,
            artefact_class=cls,
            agency=parent.agency_id,
            id=parent.id,
            version=parent.version,
            item_id=item.id,
        )
        index[str(urn)] = item

    def index_all(
        self,
        index: dict[str, object],
        objs: Iterable[SupportsUrn],
        artefact_type: type[SupportsUrn],
    ) -> None:
        """Add every artefact in *objs* to the resolution index.

        Args:
            index: The resolution index, mutated in place.
            objs: Artefacts to index. All must be of *artefact_type*.
            artefact_type: The class supplying the URN package and class.
        """
        for obj in objs:
            self.index_artefact(index, obj, artefact_type)

    def read_text_format(self, tf_el: etree._Element) -> Facet:
        """`tf_el` as a `Facet`; an absent ``isMultiLingual`` is its type's default, where it has one (#503).

        An absent ``textType`` is its context's default or fixed value, as
        an absent ``isMultiLingual`` is.
        """

        # Every bound here comes from a document attribute, so a non-numeric
        # one is a malformed document rather than a programming error — and
        # `int()` / `float()` raise a bare `ValueError` that escaped `parse`
        # as itself before #247.
        def _int(v: str | None) -> int | None:
            return to_int(v, what="a TextFormat bound") if v is not None else None

        def _float(v: str | None) -> float | None:
            return to_float(v, what="a TextFormat bound") if v is not None else None

        return Facet(
            type=tf_el.get("textType") or self._text_type_default(tf_el),
            max_length=_int(tf_el.get("maxLength")),
            min_length=_int(tf_el.get("minLength")),
            max_value=_float(tf_el.get("maxValue")),
            min_value=_float(tf_el.get("minValue")),
            decimals=_int(tf_el.get("decimals")),
            pattern=tf_el.get("pattern"),
            start_value=_float(tf_el.get("startValue")),
            end_value=_float(tf_el.get("endValue")),
            is_sequence=optional_flag(tf_el, "isSequence"),
            is_multilingual=self._multilingual(tf_el),
            interval=_float(tf_el.get("interval")),
            time_interval=tf_el.get("timeInterval"),
            start_time=tf_el.get("startTime"),
            end_time=tf_el.get("endTime"),
            # SDMX 3.x only (#422); a 2.1 format has none, and finds none.
            sentinel_values=tuple(
                SentinelValue(
                    value=required(sentinel_el, "value"),
                    name=self.read_istring(sentinel_el),
                    description=self.read_istring(sentinel_el, "Description"),
                )
                for sentinel_el in tf_el.findall("str:SentinelValue", namespaces=self._ns)
            ),
        )

    def read_enumeration_format(self, local_rep_el: etree._Element) -> Facet | None:
        """Parse ``<str:EnumerationFormat>``, the facet constraining the codes.

        Separate from `read_representation` because it is a *second* piece of
        the same element rather than an alternative to it — a component may
        carry both an Enumeration and an EnumerationFormat.
        """
        el = local_rep_el.find("str:EnumerationFormat", namespaces=self._ns)
        return self.read_text_format(el) if el is not None else None

    def read_representation[T: ParsesUrn](
        self,
        local_rep_el: etree._Element,
        index: dict[str, object] | None = None,
        *,
        enum_cls: type[T],
    ) -> Ref[T] | Facet | None:
        """Parse a LocalRepresentation or CoreRepresentation element.

        *enum_cls* is the artefact class the ``<Enumeration>`` may name, and it
        is **not** always ``Codelist``: an SDMX 2.1 ``MeasureDimension`` is
        enumerated by a `ConceptScheme` (``ConceptSchemeReferenceType`` in the
        XSD). The default covers every other component; the cross-sectional
        measure-dimension reader passes ``ConceptScheme``.

        The value is passed through to `resolve_ref`, so a URN naming some
        other class raises `SdmxConformanceError` rather than being reified
        into a slot that cannot hold it.

        When *index* is provided the ref is resolved against it. Without
        *index* (concept CoreRepresentation parsing) an unresolved Ref is
        returned — but the class check still applies.
        """
        enum_el = local_rep_el.find("str:Enumeration", namespaces=self._ns)
        if enum_el is not None:
            if index is not None:
                return self.resolve_ref(enum_el, enum_cls)
            tag = etree.QName(enum_el).localname
            ref_el = enum_el.find("Ref")
            if ref_el is not None:
                urn = self.ref_el_to_urn(ref_el, enum_cls)
                if urn is not None:
                    _require_urn_class(urn, enum_cls, tag)
                    return Ref[T].unresolved(urn)
            text = self.urn_text(enum_el)
            if text.startswith("urn:"):
                urn = enum_cls.parse_urn(text)
                _require_urn_class(urn, enum_cls, tag)
                return Ref[T].unresolved(urn)
        tf_el = local_rep_el.find("str:TextFormat", namespaces=self._ns)
        if tf_el is not None:
            return self.read_text_format(tf_el)
        return None

    def read_enumerated_representation(
        self, local_rep_el: etree._Element, index: dict[str, object] | None = None
    ) -> Representation:
        """`read_representation` for a component a codelist *or* a `ValueList` may enumerate.

        SDMX 3.x types the ``<Enumeration>`` as ``AnyCodelistReferenceType``,
        which admits a ``codelist.ValueList=`` URN beside a ``Codelist`` one
        (#422); the URN's class says which this is. SDMX-ML 2.1 has no value
        list, so there a ValueList URN is refused as `read_representation`
        refuses any wrong class.
        """
        enum_el = local_rep_el.find("str:Enumeration", namespaces=self._ns)
        if enum_el is not None and self.names_value_list(enum_el):
            return self.read_representation(local_rep_el, index, enum_cls=ValueList)
        return self.read_representation(local_rep_el, index, enum_cls=Codelist)

    def names_value_list(self, enum_el: etree._Element) -> bool:
        """True when an SDMX 3.x ``<Enumeration>`` names a `ValueList`.

        3.x types it ``AnyCodelistReferenceType``, a URN as the element's text,
        and its closed-world check refuses a ``<Ref>`` child before this runs.
        A 2.1 ``<Enumeration>`` has only children, so its text names nothing
        and it never names a value list — which 2.1 does not have.
        """
        text = (enum_el.text or "").strip()
        return text.startswith("urn:") and text.split("=", 1)[0].endswith(".ValueList")

    def read_iso_concept_reference(self, concept_el: etree._Element) -> ISOConceptReference | None:
        """A concept's ``<str:ISOConceptReference>``, its three children required (#422)."""
        iso_el = concept_el.find("str:ISOConceptReference", namespaces=self._ns)
        if iso_el is None:
            return None
        return ISOConceptReference(
            agency=iso_el.findtext("str:ConceptAgency", default="", namespaces=self._ns),
            concept_scheme_id=iso_el.findtext("str:ConceptSchemeID", default="", namespaces=self._ns),
            concept_id=iso_el.findtext("str:ConceptID", default="", namespaces=self._ns),
        )

    def read_parent_id(self, el: etree._Element) -> str | None:
        """The `<str:Parent>` id on an item, in either wire form.

        SDMX 3.0 ships `<Parent>` as a simple text element holding the parent's
        id; SDMX 2.1 wraps it in a child `<Ref>`. Both mean the same thing, so
        both are accepted here rather than in each caller.

        Shared by codes and concepts. It was inline in `read_codelists` and
        concepts simply had no equivalent — the element was parsed by nobody
        and the parent silently lost (#307). One reader means the next item
        type that gains a parent cannot quietly get a different one.
        """
        parent_el = el.find("str:Parent", namespaces=self._ns)
        if parent_el is None:
            return None
        ref_el = parent_el.find("Ref")
        if ref_el is not None:
            return required(ref_el, "id")
        if parent_el.text and parent_el.text.strip():
            return parent_el.text.strip()
        return None

    def read_codelists(
        self, structures: etree._Element, agencies: dict[str, Agency], refused: list[Refusal]
    ) -> list[Codelist]:
        """Every codelist, the two SDMX 3.x geographic kinds included.

        Those have containers of their own but are codelists — their URN class
        is `Codelist` — so they are read into the same list. 2.1 has neither
        container, and finds nothing.
        """
        return [
            *self._read_codelist_kind(structures, None, Codelist, agencies, refused),
            *self._read_codelist_kind(structures, "GeographicCodelist", GeographicCodelist, agencies, refused),
            *self._read_codelist_kind(structures, "GeoGridCodelist", GeoGridCodelist, agencies, refused),
        ]

    def _read_codelist_kind(
        self,
        structures: etree._Element,
        geo_type: str | None,
        cls: type[Codelist],
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[Codelist]:
        tag = geo_type or "Codelist"
        container = structures.find(f"str:{tag}s", namespaces=self._ns)
        if container is None:
            return []

        def one(cl_el: etree._Element) -> Codelist:
            return codelist_of_kind(
                geo_type,
                cl_el.findtext("str:GridDefinition", namespaces=self._ns),
                id=required(cl_el, "id"),
                uri=cl_el.get("uri"),
                links=self.read_links(cl_el),
                maintainer=self.resolve_agency(required(cl_el, "agencyID"), agencies),
                version=cl_el.get("version", "1.0"),
                name=self.read_istring(cl_el),
                description=self.read_istring(cl_el, "Description"),
                legacy_is_final=self.read_legacy_is_final(cl_el),
                # Any codelist may hold geographic codes: they are in `Code`'s
                # substitution group, so even a plain `<str:Codelist>` admits one.
                codes=ItemList([self._read_code(code_el) for code_el in cl_el.iterchildren(*self._code_tags())]),
                annotations=self.read_annotations(cl_el),
                valid_from=self.read_datetime_attr(cl_el, "validFrom"),
                valid_to=self.read_datetime_attr(cl_el, "validTo"),
                **self.read_reference_attrs(cl_el),
                is_partial=self.read_is_partial(cl_el),
                extensions=_read_codelist_extensions(cl_el, self._ns),
            )

        return build_each_xml(container.findall(f"str:{tag}", namespaces=self._ns), one, cls, refused)

    def _code_tags(self) -> tuple[str, str, str]:
        """The three elements a codelist's code may be, in Clark notation."""
        return (self._code, self._geo_feature_set_code, self._geo_grid_code)

    def _read_code(self, code_el: etree._Element) -> Code:
        """One `<str:Code>`, or one of its two SDMX 3.x geographic substitutes."""
        tag = code_el.tag
        return code_of_kind(
            value=required(code_el, "value") if tag == self._geo_feature_set_code else None,
            geo_cell=(
                code_el.findtext("str:GeoCell", default="", namespaces=self._ns) if tag == self._geo_grid_code else None
            ),
            id=required(code_el, "id"),
            uri=code_el.get("uri"),
            links=self.read_links(code_el),
            name=self.read_istring(code_el),
            description=self.read_istring(code_el, "Description"),
            parent_id=self.read_parent_id(code_el),
            annotations=self.read_annotations(code_el),
        )

    def read_value_lists(
        self, structures: etree._Element, agencies: dict[str, Agency], refused: list[Refusal]
    ) -> list[ValueList]:
        """Every SDMX 3.x ``<str:ValueList>`` (#422). 2.1 has none, and finds nothing."""
        container = structures.find("str:ValueLists", namespaces=self._ns)
        if container is None:
            return []

        def one(vl_el: etree._Element) -> ValueList:
            return ValueList(
                id=required(vl_el, "id"),
                uri=vl_el.get("uri"),
                links=self.read_links(vl_el),
                maintainer=self.resolve_agency(required(vl_el, "agencyID"), agencies),
                version=vl_el.get("version", "1.0"),
                name=self.read_istring(vl_el),
                description=self.read_istring(vl_el, "Description"),
                annotations=self.read_annotations(vl_el),
                valid_from=self.read_datetime_attr(vl_el, "validFrom"),
                valid_to=self.read_datetime_attr(vl_el, "validTo"),
                **self.read_reference_attrs(vl_el),
                items=ItemList(
                    [
                        ValueItem(
                            id=required(item_el, "id"),
                            name=self.read_istring(item_el),
                            description=self.read_istring(item_el, "Description"),
                            annotations=self.read_annotations(item_el),
                        )
                        for item_el in vl_el.findall("str:ValueItem", namespaces=self._ns)
                    ]
                ),
            )

        return build_each_xml(container.findall("str:ValueList", namespaces=self._ns), one, ValueList, refused)

    def read_dataflows(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[Dataflow]:
        container = structures.find("str:Dataflows", namespaces=self._ns)
        if container is None:
            return []

        def one(df_el: etree._Element) -> Dataflow:
            structure_ref: Ref[DataStructure] | None = None
            struct_el = df_el.find("str:Structure", namespaces=self._ns)
            if struct_el is not None:
                structure_ref = self.resolve_ref(struct_el, DataStructure)
            # `DimensionConstraint` is 3.1's alone; 2.1 and 3.0 documents have
            # no such element, so this finds nothing there and needs no version
            # branch. Read here rather than in the 3.1 reader because all three
            # share this method (#355).
            dc_el = df_el.find("str:DimensionConstraint", namespaces=self._ns)
            dimension_constraint = (
                ()
                if dc_el is None
                else tuple(
                    d.text.strip()
                    for d in dc_el.findall("str:Dimension", namespaces=self._ns)
                    if d.text and d.text.strip()
                )
            )
            return Dataflow(
                id=required(df_el, "id"),
                uri=df_el.get("uri"),
                links=self.read_links(df_el),
                maintainer=self.resolve_agency(required(df_el, "agencyID"), agencies),
                version=df_el.get("version", "1.0"),
                legacy_is_final=self.read_legacy_is_final(df_el),
                name=self.read_istring(df_el),
                description=self.read_istring(df_el, "Description"),
                annotations=self.read_annotations(df_el),
                structure=structure_ref,
                dimension_constraint=dimension_constraint,
                valid_from=self.read_datetime_attr(df_el, "validFrom"),
                valid_to=self.read_datetime_attr(df_el, "validTo"),
                **self.read_reference_attrs(df_el),
            )

        return build_each_xml(container.findall("str:Dataflow", namespaces=self._ns), one, Dataflow, refused)

    def read_categories(self, parent_el: etree._Element) -> list[Category]:
        """Recursively read Category elements."""
        return [
            Category(
                id=required(cat_el, "id"),
                uri=cat_el.get("uri"),
                links=self.read_links(cat_el),
                name=self.read_istring(cat_el),
                description=self.read_istring(cat_el, "Description"),
                categories=ItemList(self.read_categories(cat_el)),
                annotations=self.read_annotations(cat_el),
            )
            for cat_el in parent_el.findall("str:Category", namespaces=self._ns)
        ]

    def read_category_schemes(
        self, structures: etree._Element, agencies: dict[str, Agency], refused: list[Refusal]
    ) -> list[CategoryScheme]:
        container = structures.find("str:CategorySchemes", namespaces=self._ns)
        if container is None:
            return []

        def one(cs_el: etree._Element) -> CategoryScheme:
            return CategoryScheme(
                id=required(cs_el, "id"),
                uri=cs_el.get("uri"),
                links=self.read_links(cs_el),
                maintainer=self.resolve_agency(required(cs_el, "agencyID"), agencies),
                version=cs_el.get("version", "1.0"),
                legacy_is_final=self.read_legacy_is_final(cs_el),
                name=self.read_istring(cs_el),
                description=self.read_istring(cs_el, "Description"),
                categories=ItemList(self.read_categories(cs_el)),
                annotations=self.read_annotations(cs_el),
                valid_from=self.read_datetime_attr(cs_el, "validFrom"),
                valid_to=self.read_datetime_attr(cs_el, "validTo"),
                **self.read_reference_attrs(cs_el),
                is_partial=self.read_is_partial(cs_el),
            )

        return build_each_xml(
            container.findall("str:CategoryScheme", namespaces=self._ns), one, CategoryScheme, refused
        )

    def read_categorisations(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[Categorisation]:
        container = structures.find("str:Categorisations", namespaces=self._ns)
        if container is None:
            return []

        def one(cat_el: etree._Element) -> Categorisation:
            # Categorisation source/target are genuinely heterogeneous (source is
            # any categorisable artefact, target a Category) → Ref[object].
            source_ref: Ref[object] | None = None
            source_el = cat_el.find("str:Source", namespaces=self._ns)
            if source_el is not None:
                source_ref = self.resolve_ref(source_el, object)
            target_ref: Ref[object] | None = None
            target_el = cat_el.find("str:Target", namespaces=self._ns)
            if target_el is not None:
                target_ref = self.resolve_ref(target_el, object)
            return Categorisation(
                id=required(cat_el, "id"),
                uri=cat_el.get("uri"),
                links=self.read_links(cat_el),
                maintainer=self.resolve_agency(required(cat_el, "agencyID"), agencies),
                version=cat_el.get("version", "1.0"),
                legacy_is_final=self.read_legacy_is_final(cat_el),
                name=self.read_istring(cat_el),
                description=self.read_istring(cat_el, "Description"),
                annotations=self.read_annotations(cat_el),
                source=source_ref,
                target=target_ref,
                valid_from=self.read_datetime_attr(cat_el, "validFrom"),
                valid_to=self.read_datetime_attr(cat_el, "validTo"),
                **self.read_reference_attrs(cat_el),
            )

        return build_each_xml(
            container.findall("str:Categorisation", namespaces=self._ns), one, Categorisation, refused
        )

    def read_provision_agreements(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[ProvisionAgreement]:
        container = structures.find("str:ProvisionAgreements", namespaces=self._ns)
        if container is None:
            return []

        def one(pa_el: etree._Element) -> ProvisionAgreement:
            dataflow_ref: Ref[Dataflow] | None = None
            # SDMX 2.1 uses <StructureUsage>; SDMX 3.0 uses <Dataflow>.
            # Accept either to keep this helper shared across versions.
            ref_el = pa_el.find("str:Dataflow", namespaces=self._ns)
            if ref_el is None:
                ref_el = pa_el.find("str:StructureUsage", namespaces=self._ns)
            if ref_el is not None:
                dataflow_ref = self.resolve_ref(ref_el, Dataflow)
            provider_ref: Ref[object] | None = None
            dp_el = pa_el.find("str:DataProvider", namespaces=self._ns)
            if dp_el is not None:
                provider_ref = self.resolve_ref(dp_el, object)
            return ProvisionAgreement(
                id=required(pa_el, "id"),
                uri=pa_el.get("uri"),
                links=self.read_links(pa_el),
                maintainer=self.resolve_agency(required(pa_el, "agencyID"), agencies),
                version=pa_el.get("version", "1.0"),
                legacy_is_final=self.read_legacy_is_final(pa_el),
                name=self.read_istring(pa_el),
                description=self.read_istring(pa_el, "Description"),
                annotations=self.read_annotations(pa_el),
                dataflow=dataflow_ref,
                data_provider=provider_ref,
                valid_from=self.read_datetime_attr(pa_el, "validFrom"),
                valid_to=self.read_datetime_attr(pa_el, "validTo"),
                **self.read_reference_attrs(pa_el),
            )

        # One over a metadataflow is 2.1's metadata provision agreement, which
        # the 2.1 reader builds as one (#421).
        elements = [
            el
            for el in container.findall("str:ProvisionAgreement", namespaces=self._ns)
            if not self.provisions_metadata(el)
        ]
        return build_each_xml(elements, one, ProvisionAgreement, refused)

    def provisions_metadata(self, pa_el: etree._Element) -> bool:
        """Whether a ``<str:ProvisionAgreement>`` is over a metadataflow: 2.1's form of metadata provisioning.

        Only 2.1 can say so, its ``StructureUsage`` naming a dataflow or a
        metadataflow; 3.x has an element of its own for each (#421).
        """
        usage = pa_el.find("str:StructureUsage", namespaces=self._ns)
        if usage is None:
            return False
        ref_el = usage.find("Ref")
        if ref_el is not None:
            # Its class is all this needs. The rest of the ref is the build's
            # to check: this runs before any build, outside the catch that
            # refuses one artefact rather than failing the document (#474).
            return ref_el.get("class") == "Metadataflow"
        text = self.urn_text(usage)
        return text.startswith("urn:") and SdmxUrn.parse(text).artefact_class == "Metadataflow"

    def read_reference_period(self, cc_el: etree._Element) -> ReferencePeriod | None:
        rp_el = cc_el.find("str:ReferencePeriod", namespaces=self._ns)
        if rp_el is None:
            return None
        return ReferencePeriod(
            start_period=to_datetime(required(rp_el, "startTime"), what="<ReferencePeriod> @startTime"),
            end_period=to_datetime(required(rp_el, "endTime"), what="<ReferencePeriod> @endTime"),
        )

    def read_release_calendar(self, cc_el: etree._Element) -> ReleaseCalendar | None:
        rc_el = cc_el.find("str:ReleaseCalendar", namespaces=self._ns)
        if rc_el is None:
            return None
        periodicity_el = rc_el.find("str:Periodicity", namespaces=self._ns)
        offset_el = rc_el.find("str:Offset", namespaces=self._ns)
        tolerance_el = rc_el.find("str:Tolerance", namespaces=self._ns)
        return ReleaseCalendar(
            periodicity=periodicity_el.text if periodicity_el is not None and periodicity_el.text else "",
            offset=offset_el.text if offset_el is not None and offset_el.text else "",
            tolerance=tolerance_el.text if tolerance_el is not None and tolerance_el.text else "",
        )

    def read_concept_schemes(
        self, structures: etree._Element, agencies: dict[str, Agency], refused: list[Refusal]
    ) -> list[ConceptScheme]:
        # SDMX 3.0 renamed the container element Concepts → ConceptSchemes.
        container = structures.find("str:ConceptSchemes", namespaces=self._ns)
        if container is None:
            container = structures.find("str:Concepts", namespaces=self._ns)
        if container is None:
            return []

        def one(cs_el: etree._Element) -> ConceptScheme:
            concepts: list[Concept] = []
            for concept_el in cs_el.findall("str:Concept", namespaces=self._ns):
                core_rep = None
                enumeration_format = None
                core_rep_el = concept_el.find("str:CoreRepresentation", namespaces=self._ns)
                # SDMX 3.x's; a 2.1 core representation has none, so 1, 1 (#422).
                min_occurs, max_occurs = self.read_occurs(core_rep_el)
                if core_rep_el is not None:
                    core_rep = self.read_enumerated_representation(core_rep_el)
                    ef_el = core_rep_el.find("str:EnumerationFormat", namespaces=self._ns)
                    enumeration_format = self.read_text_format(ef_el) if ef_el is not None else None
                concepts.append(
                    Concept(
                        id=required(concept_el, "id"),
                        uri=concept_el.get("uri"),
                        links=self.read_links(concept_el),
                        name=self.read_istring(concept_el),
                        description=self.read_istring(concept_el, "Description"),
                        core_representation=core_rep,
                        enumeration_format=enumeration_format,
                        min_occurs=min_occurs,
                        max_occurs=max_occurs,
                        iso_concept_reference=self.read_iso_concept_reference(concept_el),
                        parent_id=self.read_parent_id(concept_el),
                        annotations=self.read_annotations(concept_el),
                    )
                )
            return ConceptScheme(
                id=required(cs_el, "id"),
                uri=cs_el.get("uri"),
                links=self.read_links(cs_el),
                maintainer=self.resolve_agency(required(cs_el, "agencyID"), agencies),
                version=cs_el.get("version", "1.0"),
                legacy_is_final=self.read_legacy_is_final(cs_el),
                name=self.read_istring(cs_el),
                description=self.read_istring(cs_el, "Description"),
                concepts=ItemList(concepts),
                annotations=self.read_annotations(cs_el),
                valid_from=self.read_datetime_attr(cs_el, "validFrom"),
                valid_to=self.read_datetime_attr(cs_el, "validTo"),
                **self.read_reference_attrs(cs_el),
                is_partial=self.read_is_partial(cs_el),
            )

        return build_each_xml(container.findall("str:ConceptScheme", namespaces=self._ns), one, ConceptScheme, refused)

    def read_data_key_sets(self, el: etree._Element) -> tuple[DataKeySet, ...]:
        """Every ``<str:DataKeySet>`` child, each with its required ``isIncluded`` (#479).

        Read flat, the keys of an excluded set came back as allowed, the
        opposite of the document, and nothing reported it.
        """
        return tuple(
            DataKeySet(
                is_included=required_flag(ks_el, "isIncluded"),
                keys=tuple(self._read_data_key(key_el) for key_el in ks_el.findall("str:Key", namespaces=self._ns)),
            )
            for ks_el in el.findall("str:DataKeySet", namespaces=self._ns)
        )

    def _read_data_key(self, key_el: etree._Element) -> DataKey:
        return DataKey(
            values=self._read_key_values(key_el),
            annotations=self.read_annotations(key_el),
            valid_from=key_el.get("validFrom"),
            valid_to=key_el.get("validTo"),
            components=tuple(
                self._read_component_value_set(el) for el in key_el.findall("str:Component", namespaces=self._ns)
            ),
        )

    def _read_key_values(self, key_el: etree._Element) -> tuple[tuple[str, str], ...]:
        """The (dimension, value) pairs of one ``<str:Key>``.

        The child lives in the *common* namespace in 2.1 and the *structure*
        namespace in 3.0/3.1, so both are tried. Reading only one silently
        produced keys with no values — a `DataKey(values=())` that looks
        populated at the set level and is empty underneath.
        """
        for prefix in ("com", "str"):
            pairs = [
                (required(kv_el, "id"), (v_el.text or "").strip())
                for kv_el in key_el.findall(f"{prefix}:KeyValue", namespaces=self._ns)
                if (v_el := kv_el.find(f"{prefix}:Value", namespaces=self._ns)) is not None
            ]
            if pairs:
                return tuple(pairs)
        return ()

    def _either(self, el: etree._Element, name: str) -> list[etree._Element]:
        """`name` children in either namespace.

        3.0 moved constraint content from `com:` to `str:`, so a reader for
        both accepts both.
        """
        return el.findall(f"str:{name}", namespaces=self._ns) + el.findall(f"com:{name}", namespaces=self._ns)

    def _read_time_range(self, selection_el: etree._Element) -> TimeRange | None:
        """`TimeRange`: `BeforePeriod` | `AfterPeriod` | `StartPeriod` + `EndPeriod` (#433)."""
        found = self._either(selection_el, "TimeRange")
        if not found:
            return None

        def bound(*names: str) -> TimeBound | None:
            for name in names:
                for el in self._either(found[0], name):
                    return TimeBound(period=(el.text or "").strip(), inclusive=flag(el, "isInclusive", default=True))
            return None

        return TimeRange(
            start=bound("StartPeriod", "AfterPeriod"),
            end=bound("EndPeriod", "BeforePeriod"),
            valid_from=found[0].get("validFrom"),
            valid_to=found[0].get("validTo"),
        )

    def _values(self, selection_el: etree._Element) -> frozenset[RegionValue]:
        """A value set's values with their qualifiers (#422): cascade, language, validity."""
        return frozenset(
            RegionValue(
                value=v.text,
                cascade=(
                    to_enum(CascadeSelection, cascade, what="<Value> @cascadeValues")
                    if (cascade := v.get("cascadeValues")) is not None
                    else None
                ),
                lang=optional_xml_tag(v, "Value"),
                valid_from=v.get("validFrom"),
                valid_to=v.get("validTo"),
            )
            for v in self._either(selection_el, "Value")
            if v.text
        )

    def _read_cube_region(self, region_el: etree._Element) -> CubeRegion:
        """One `CubeRegion`, with its time ranges and component value sets.

        Both were dropped until #433: a key's `TimeRange`, and 2.1's
        `com:Attribute` / 3.0's `str:Component`.

        A value set's own ``include`` is read too: ``"false"`` excludes its
        values, and was read as an inclusion until #422.
        """
        keys = tuple(
            KeyValue(
                dimension_id=required(el, "id"),
                values=self._values(el),
                time_range=self._read_time_range(el),
                include=flag(el, "include", default=True),
                remove_prefix=flag(el, "removePrefix"),
                valid_from=el.get("validFrom"),
                valid_to=el.get("validTo"),
            )
            for el in self._either(region_el, "KeyValue")
        )
        components = tuple(
            self._read_component_value_set(el)
            for el in self._either(region_el, "Component") + self._either(region_el, "Attribute")
        )
        return CubeRegion(
            include=flag(region_el, "include", default=True),
            keys=keys,
            components=components,
            annotations=self.read_annotations(region_el),
        )

    def _read_component_value_set(self, el: etree._Element) -> ComponentValueSet:
        """One ``<Component>`` — a cube region's or, in 3.x, a data key's (#422)."""
        return ComponentValueSet(
            component_id=required(el, "id"),
            values=self._values(el),
            time_range=self._read_time_range(el),
            include=flag(el, "include", default=True),
            remove_prefix=flag(el, "removePrefix"),
        )

    def constrains_metadata(self, cc_el: etree._Element) -> bool:
        """Whether a 2.1 ``ContentConstraint`` or ``AttachmentConstraint`` constrains metadata.

        2.1 uses each class for data and metadata alike; which one an instance
        is shows only in what it attaches to and what it holds. 3.0 split
        them, and its ``MetadataConstraint`` is refused — sdmxlib does not
        model it. A 2.1 one was read as a *data* constraint, its attachment
        and metadata regions dropped, and nothing reported (#422).
        """
        att_el = cc_el.find("str:ConstraintAttachment", namespaces=self._ns)
        attached = att_el is not None and any(
            att_el.find(f"str:{tag}", namespaces=self._ns) is not None
            for tag in ("MetadataStructure", "Metadataflow", "MetadataSet")
        )
        return attached or any(
            cc_el.find(f"str:{tag}", namespaces=self._ns) is not None
            for tag in ("MetadataKeySet", "MetadataTargetRegion")
        )

    def read_constraint_attachment(self, att_el: etree._Element | None) -> ConstraintAttachment:
        """A ``<str:ConstraintAttachment>``, every alternative and every artefact in it (#422).

        Until #422 the first structure, flow, agreement or provider was kept and
        the rest — further artefacts, data sources, 2.1's data sets — dropped.
        What is read is not checked for the schema's choice; `validate` does that.
        """
        if att_el is None:
            return ConstraintAttachment()
        ns = self._ns
        attached = att_el

        def refs(tag: str) -> tuple[Ref[object], ...]:
            found = (self.resolve_ref(el, object) for el in attached.findall(f"str:{tag}", namespaces=ns))
            return tuple(ref for ref in found if ref is not None)

        provider_el = att_el.find("str:DataProvider", namespaces=ns)
        return ConstraintAttachment(
            data_provider=self.resolve_ref(provider_el, object) if provider_el is not None else None,
            data_sets=tuple(self._read_data_set_reference(el) for el in att_el.findall("str:DataSet", namespaces=ns)),
            simple_data_sources=tuple(
                (el.text or "").strip() for el in att_el.findall("str:SimpleDataSource", namespaces=ns)
            ),
            data_structures=refs("DataStructure"),
            dataflows=refs("Dataflow"),
            provision_agreements=refs("ProvisionAgreement"),
            queryable_data_sources=tuple(
                self._read_queryable_data_source(el) for el in att_el.findall("str:QueryableDataSource", namespaces=ns)
            ),
        )

    def _read_data_set_reference(self, el: etree._Element) -> DataSetReference:
        """2.1's ``<str:DataSet>``: a ``com:DataProvider`` and a ``com:ID``."""
        provider_el = el.find("com:DataProvider", namespaces=self._ns)
        provider = self.resolve_ref(provider_el, object) if provider_el is not None else None
        if provider is None:
            detail = "SetReferenceType requires a <com:DataProvider>; this names none"
            raise SdmxConformanceError(etree.QName(el).localname, detail)
        return DataSetReference(
            data_provider=provider, id=(el.findtext("com:ID", default="", namespaces=self._ns)).strip()
        )

    def _read_queryable_data_source(self, el: etree._Element) -> QueryableDataSource:
        """A ``<str:QueryableDataSource>`` — ``QueryableDataSourceType`` in every version."""
        ns = self._ns
        return QueryableDataSource(
            data_url=el.findtext("com:DataURL", default="", namespaces=ns).strip(),
            is_rest_datasource=required_flag(el, "isRESTDatasource"),
            is_web_service_datasource=required_flag(el, "isWebServiceDatasource"),
            wsdl_url=el.findtext("com:WSDLURL", namespaces=ns),
            wadl_url=el.findtext("com:WADLURL", namespaces=ns),
        )

    def read_constraints(
        self,
        structures: etree._Element,
        agencies: dict[str, Agency],
        refused: list[Refusal],
    ) -> list[DataConstraint]:
        """Parse Constraints — DataConstraint (3.0+) and ContentConstraint (2.1).

        SDMX 3.0 split the unified Constraints container into DataConstraints
        and MetadataConstraints. Accept either container name for robustness.
        """
        containers = [
            c
            for c in (
                structures.find("str:DataConstraints", namespaces=self._ns),
                structures.find("str:Constraints", namespaces=self._ns),
            )
            if c is not None
        ]
        if not containers:
            return []
        # SDMX 3.0 renamed ContentConstraint → DataConstraint; handle both element names
        cc_els: list[etree._Element] = []
        for container in containers:
            cc_els.extend(container.findall("str:DataConstraint", namespaces=self._ns))
            for cc_el in container.findall("str:ContentConstraint", namespaces=self._ns):
                if self.constrains_metadata(cc_el):
                    refuse_xml(cc_el, DataConstraint, METADATA_CONSTRAINT, refused)
                else:
                    cc_els.append(cc_el)

        def one(cc_el: etree._Element) -> DataConstraint:
            attach = self.read_constraint_attachment(cc_el.find("str:ConstraintAttachment", namespaces=self._ns))

            regions = [
                self._read_cube_region(region_el) for region_el in cc_el.findall("str:CubeRegion", namespaces=self._ns)
            ]

            return DataConstraint(
                id=required(cc_el, "id"),
                uri=cc_el.get("uri"),
                links=self.read_links(cc_el),
                maintainer=self.resolve_agency(required(cc_el, "agencyID"), agencies),
                version=cc_el.get("version", "1.0"),
                name=self.read_istring(cc_el),
                description=self.read_istring(cc_el, "Description"),
                legacy_is_final=self.read_legacy_is_final(cc_el),
                constraint_type=_read_constraint_type(cc_el),
                attachment=attach,
                regions=tuple(regions),
                data_key_sets=self.read_data_key_sets(cc_el),
                reference_period=self.read_reference_period(cc_el),
                release_calendar=self.read_release_calendar(cc_el),
                annotations=self.read_annotations(cc_el),
                valid_from=self.read_datetime_attr(cc_el, "validFrom"),
                valid_to=self.read_datetime_attr(cc_el, "validTo"),
                **self.read_reference_attrs(cc_el),
            )

        return build_each_xml(cc_els, one, DataConstraint, refused)

    def read_availability_constraints(
        self, structures: etree._Element, refused: list[Refusal]
    ) -> list[AvailabilityConstraint]:
        """SDMX-ML 3.1's ``<str:AvailabilityConstraints>`` (#421).

        Each is refused alone, as an artefact is (#435), and named by its
        position: it has no id, agency or version to name it by.
        """
        container = structures.find("str:AvailabilityConstraints", namespaces=self._ns)
        if container is None:
            return []
        built: list[AvailabilityConstraint] = []
        for n, el in enumerate(container.findall("str:AvailabilityConstraint", namespaces=self._ns), start=1):
            try:
                check_artefact(el)
                built.append(self._read_availability_constraint(el))
            except SdmxConformanceError as exc:
                name = AvailabilityConstraint.__name__
                refused.append(Refusal(artefact_class=name, urn=f"{name}[{n}]", reason=str(exc)))
        return built

    def _read_availability_constraint(self, el: etree._Element) -> AvailabilityConstraint:
        region_el = el.find("str:CubeRegion", namespaces=self._ns)
        if region_el is None:
            detail = "AvailabilityConstraintType requires a <CubeRegion>"
            raise SdmxConformanceError(etree.QName(el).localname, detail)
        return AvailabilityConstraint(
            attachment=self.read_constraint_attachment(el.find("str:ConstraintAttachment", namespaces=self._ns)),
            region=self._read_cube_region(region_el),
            series_count=_count(el, "seriesCount"),
            obs_count=_count(el, "obsCount"),
            annotations=self.read_annotations(el),
        )


def _count(el: etree._Element, attr: str) -> int | None:
    """An ``xs:int`` count attribute, or None when absent."""
    raw = el.get(attr)
    return None if raw is None else to_int(raw, what=f"@{attr}")


def _read_constraint_type(cc_el: etree._Element) -> ConstraintType:
    """The constraint's role, as the enum, or a conformance error.

    SDMX 3.0 spells it `role` and 2.1 spells it `type`; either may hold a
    value the enumeration does not permit. `ConstraintType.parse` then raises a
    bare `ValueError`, which is right for a caller constructing the model
    directly and wrong at a format boundary — a reader's contract is that a
    document contradicting the schema surfaces as `SdmxConformanceError`, and
    `parse()` promises to raise nothing else.

    The fuzzer reached it with `type=""`, which slips past the `or` below
    because an empty string is falsy and then fails the enum lookup.

    Absent, the value is each version's own: 2.1's ``@type`` is optional with
    ``default="Actual"`` (`SDMXStructureConstraint.xsd`), and was read as
    Allowed until #475. 3.1 has no ``@role`` at all, its Actual content moving
    to `AvailabilityConstraint`, so a `DataConstraint` there is Allowed. 3.0
    requires ``@role``, so a missing one is an assumption, #474's to record.
    """
    absent = "Actual" if BY_STRUCTURE_NS.get(etree.QName(cc_el).namespace or "") == "2.1" else "Allowed"
    role = cc_el.get("role") or cc_el.get("type", absent)
    try:
        return ConstraintType.parse(role)
    except ValueError as exc:
        attribute = "role" if cc_el.get("role") is not None else "type"
        raise SdmxConformanceError(
            etree.QName(cc_el).localname,
            f"{attribute}={role!r} on constraint {cc_el.get('id', '')!r} is not a "
            f"value the SDMX schema permits; expected Allowed or Actual.",
        ) from exc


def _read_codelist_extensions(cl_el: etree._Element, ns: dict[str, str]) -> tuple[CodelistExtension, ...]:
    """Parse zero-or-more ``<str:CodelistExtension>`` children of a Codelist.

    SDMX 3.0 / 3.1 only — the element doesn't exist in 2.1, so the helper
    returns an empty tuple when called on a 2.1 codelist. Order is
    preserved per the XSD's precedence guidance.
    """
    out: list[CodelistExtension] = []
    for ext_el in cl_el.findall("str:CodelistExtension", namespaces=ns):
        cl_ref_el = ext_el.find("str:Codelist", namespaces=ns)
        if cl_ref_el is None or cl_ref_el.text is None:
            continue
        urn_text = cl_ref_el.text.strip()
        if not urn_text.startswith("urn:"):
            continue
        codelist_ref = Ref[Codelist].unresolved(Codelist.parse_urn(urn_text))

        selection: CodeSelection | None = None
        sel_el = ext_el.find("str:InclusiveCodeSelection", namespaces=ns)
        mode = InclusionMode.INCLUDE
        if sel_el is None:
            sel_el = ext_el.find("str:ExclusiveCodeSelection", namespaces=ns)
            mode = InclusionMode.EXCLUDE
        if sel_el is not None:
            members: list[MemberValue] = []
            for mv_el in sel_el.findall("str:MemberValue", namespaces=ns):
                if mv_el.text is None:
                    continue
                cascade_attr = mv_el.get("cascadeValues")
                cascade = (
                    to_enum(CascadeSelection, cascade_attr, what="<MemberValue> @cascadeValues")
                    if cascade_attr is not None
                    else None
                )
                members.append(MemberValue(value=mv_el.text.strip(), cascade=cascade))
            selection = CodeSelection(mode=mode, members=tuple(members))

        out.append(CodelistExtension(codelist=codelist_ref, selection=selection, prefix=ext_el.get("prefix")))
    return tuple(out)


#: Hierarchy URN → the id of the 2.1 `HierarchicalCodelist` written around it,
#: for the duration of one 2.1 `write`. A reference to a hierarchy node must name
#: that container (`HierarchicalCodeRefType@maintainableParentID`) as well as
#: the hierarchy (`@containerID`), and only the message knows which declared
#: container claims a hierarchy. A context variable rather than writer state:
#: `WriterHelpers` is one shared instance per format, and writes may overlap.
_HCL_OF: ContextVar[Mapping[str, str] | None] = ContextVar("sdmxlib_hcl_of", default=None)


@contextmanager
def hierarchy_containers(hcl_of: Mapping[str, str]) -> Generator[None]:
    """Make *hcl_of* the hierarchy → HCL id map for references written inside."""
    token = _HCL_OF.set(hcl_of)
    try:
        yield
    finally:
        _HCL_OF.reset(token)


class WriterHelpers:
    """Namespace-bound SDMX-ML writer helpers.

    A class rather than a closure factory: each method is scored, navigated and
    tested on its own, where 22 nested ``def``s in one function were not. The
    two forms measured identically (12.4 ns vs 12.5 ns per call), so this costs
    nothing at runtime — see #209.
    """

    def __init__(self, *, com: str, str_: str, mes: str, grammar: str) -> None:
        """Bind the three SDMX-ML namespace URIs and pre-build the hot tags.

        `grammar` is the SDMX-ML version whose `VersionType` this writer must
        satisfy — `"2.1"` or `"3.0"`, the latter shared by 3.1. Needed because
        the two grammars are not nested: 2.1 permits `1.03` and `1.2.3.4`
        which 3.0 forbids, and 3.0 permits `1.0.0-draft` which 2.1 forbids
        (#319). A writer that emits a version its own schema rejects produces
        a document that fails XSD validation, which the round-trip tests catch
        and a user never sees.
        """
        self.com = com
        self.str_ = str_
        self.mes = mes
        self.grammar = grammar
        #: The version written, `formats.gaps`' name for it: what the schema
        #: defaults are (#503). `grammar` serves 3.0 and 3.1 both, so the 3.x
        #: writer sets this from its generation.
        self.target = grammar
        #: Whether this version writes `@isPartialLanguage` — 3.1's alone
        #: (#422). The 3.x writer sets it from its generation; one `grammar`
        #: serves 3.0 and 3.1 both, so it cannot say.
        self.partial_language = False
        self._c_name = f"{{{com}}}Name"
        self._c_description = f"{{{com}}}Description"
        self._c_annotations = f"{{{com}}}Annotations"
        self._c_annotation = f"{{{com}}}Annotation"
        self._c_annotation_title = f"{{{com}}}AnnotationTitle"
        self._c_annotation_type = f"{{{com}}}AnnotationType"
        self._c_annotation_url = f"{{{com}}}AnnotationURL"
        self._c_annotation_text = f"{{{com}}}AnnotationText"
        self._c_annotation_value = f"{{{com}}}AnnotationValue"
        self._s_code = f"{{{str_}}}Code"
        self._s_parent = f"{{{str_}}}Parent"

    def c(self, tag: str) -> str:
        """Qualify *tag* with the **common** namespace (``com:``).

        Returns lxml's Clark notation, ``{uri}tag`` — the form `etree.SubElement`
        and `etree.Element` require, since they resolve no prefixes.

        Example:
            self.c("Name")  # "{http://…/common}Name"
        """
        return f"{{{self.com}}}{tag}"

    def s(self, tag: str) -> str:
        """Qualify *tag* with the **structure** namespace (``str:``).

        The bulk of SDMX-ML lives here — Codelist, Dataflow, DataStructure and
        every component. See `c` for the notation.
        """
        return f"{{{self.str_}}}{tag}"

    def m(self, tag: str) -> str:
        """Qualify *tag* with the **message** namespace (``mes:``).

        The envelope only: Structure, Header, Structures. See `c` for the
        notation.
        """
        return f"{{{self.mes}}}{tag}"

    def write_istring(
        self,
        parent: etree._Element,
        istring: InternationalString | dict[str, str],
        child_tag: Literal["Name", "Description"] = "Name",
    ) -> None:
        """Append com:Name (or com:Description) children for each language."""
        tag = self._c_name if child_tag == "Name" else self._c_description
        texts = istring._backing() if isinstance(istring, InternationalString) else istring
        for lang, text in texts.items():
            el = etree.SubElement(parent, tag)
            el.set(XML_LANG, lang)
            el.text = text

    def write_links(self, parent: etree._Element, links: tuple[Link, ...]) -> None:
        """Emit SDMX 3.x's ``<com:Link>`` elements, which follow ``<com:Annotations>`` (#422).

        SDMX-ML 2.1 has no such element, so a 2.1 write writes none — declared
        in `formats/gaps.py`, so the drop is answerable before writing.
        """
        if self.grammar == "2.1":
            return
        for link in links:
            link_el = etree.SubElement(parent, self.c("Link"))
            link_el.set("rel", link.rel)
            link_el.set("url", link.url)
            if link.urn is not None:
                link_el.set("urn", link.urn)
            if link.type is not None:
                link_el.set("type", link.type)

    def write_annotations(self, parent: etree._Element, annotations: Annotations) -> None:
        """Emit a ``<com:Annotations>`` block as the first child of *parent*.

        Per every SDMX-ML XSD where annotations appear, the
        ``<com:Annotations>`` element must come BEFORE Name / Description.
        Callers are responsible for invoking this in the right
        position; empty/None inputs no-op.
        """
        if not annotations:
            return
        anns_el = etree.SubElement(parent, self._c_annotations)
        for ann in annotations:
            ann_el = etree.SubElement(anns_el, self._c_annotation)
            if ann.id is not None:
                ann_el.set("id", ann.id)
            if ann.title is not None:
                etree.SubElement(ann_el, self._c_annotation_title).text = ann.title
            if ann.type is not None:
                etree.SubElement(ann_el, self._c_annotation_type).text = ann.type
            # 2.1 has one unlocalised URL; the rest are a declared gap.
            for url in ann.urls[:1] if self.grammar == "2.1" else ann.urls:
                url_el = etree.SubElement(ann_el, self._c_annotation_url)
                url_el.text = url.url
                if url.lang is not None and self.grammar != "2.1":
                    url_el.set(XML_LANG, url.lang)
            for lang, text in ann.text._backing().items():
                text_el = etree.SubElement(ann_el, self._c_annotation_text)
                text_el.set(XML_LANG, lang)
                text_el.text = text
            # Last in the sequence, after AnnotationText — the XSD orders it so,
            # and an element out of order is rejected outright. 2.1's
            # `AnnotationType` has no such child, so emitting it there would
            # produce a non-conformant document; the loss is declared in
            # `formats/gaps.py` rather than silently papered over (#368).
            if ann.value is not None and self.grammar != "2.1":
                etree.SubElement(ann_el, self._c_annotation_value).text = ann.value

    @staticmethod
    def write_uri(el: etree._Element, obj: SupportsUri) -> None:
        """Set ``@uri`` when the artefact carries one.

        Declared on ``IdentifiableType``, so it applies to every maintainable,
        item and component alike — which is why this is a free-standing helper
        rather than part of `write_maintainable_attrs`. Optional in all three
        SDMX-ML versions, so an absent uri emits no attribute at all (#238).
        """
        if obj.uri:
            el.set("uri", obj.uri)

    #: 2.1 pins `isFinal` to `false` on the organisation schemes —
    #: `AgencyScheme`, `DataProviderScheme` and `DataConsumerScheme` all carry
    #: `use="optional" fixed="false"`. So the attribute exists there and only
    #: one value is legal, which for writing purposes is the same as not having
    #: it. Named by class rather than probed, because "the XSD fixes this
    #: attribute" is not something the model knows or should.
    _IS_FINAL_FIXED_FALSE_IN_21 = frozenset({"AgencyScheme", "DataProviderScheme", "DataConsumerScheme"})

    def carries_is_final(self, obj: MaintainableArtefact) -> bool:
        """Whether this grammar can express a final artefact as `isFinal="true"`.

        Two separate reasons it cannot, both found by #354's field census and
        both previously emitted anyway (#357):

        * **3.0 and 3.1 have no `isFinal`.** It was removed from every
          maintainable and is not in the XSD, so emitting it is a document no
          conformant parser accepts. `gaps.py` had declared this a limit since
          #159 while the writer kept setting the attribute.
        * **2.1 fixes it to `false` on the organisation schemes.** Emitting
          `true` there fails validation just as surely.

        Neither was caught because no test combined a final artefact with XSD
        validation — the flag was set in fixtures that were not validated, and
        validated fixtures did not set the flag.
        """
        if self.grammar != "2.1":
            return False
        return type(obj).__name__ not in self._IS_FINAL_FIXED_FALSE_IN_21

    def write_maintainable_attrs(self, el: etree._Element, obj: MaintainableArtefact) -> None:
        """Set id, agencyID, version, isFinal, uri on a maintainable element.

        Raises:
            ValidationError: If the version cannot be expressed in this
                writer's SDMX-ML version. `serialize` refuses it first, in
                `check_serialisable`; this holds for a caller writing directly
                (#319).
        """
        check_writable(obj.version, self.grammar, artefact=f"{type(obj).__name__}:{obj.id}")
        el.set("id", obj.id)
        el.set("agencyID", obj.agency_id)
        el.set("version", obj.version)
        self.write_uri(el, obj)
        # `legacy_is_final` / `valid_from` / `valid_to` are subclass-specific —
        # they are not on `MaintainableArtefact`, and a `getattr` guard does
        # not narrow the later direct read. Bind annotated locals so the `Any`
        # that getattr returns dies here.
        #
        # 2.1's `isFinal` is the document's own where one was read and
        # disagreed with the version, and the version's otherwise (#424).
        legacy: bool | None = getattr(obj, "legacy_is_final", None)
        if self.carries_is_final(obj) and (obj.is_final if legacy is None else legacy):
            el.set("isFinal", "true")
        valid_from: datetime | None = getattr(obj, "valid_from", None)
        if valid_from is not None:
            el.set("validFrom", valid_from.isoformat())
        valid_to: datetime | None = getattr(obj, "valid_to", None)
        if valid_to is not None:
            el.set("validTo", valid_to.isoformat())
        self.write_reference_attrs(el, obj)

    def write_reference_attrs(self, el: etree._Element, obj: MaintainableArtefact) -> None:
        """Set a maintainable's external-reference and partiality attributes (#422).

        Each only where it says something its default does not. Every SDMX-ML
        version allows the first three on any maintainable, and ``isPartial``
        on an item scheme — the only classes with an `is_partial` field.
        ``isPartialLanguage`` is 3.1's alone: `partial_language` says whether
        this writer's version has it.
        """
        if self.partial_language and obj.is_partial_language:
            el.set("isPartialLanguage", "true")
        if obj.is_external_reference:
            el.set("isExternalReference", "true")
        if obj.service_url is not None:
            el.set("serviceURL", obj.service_url)
        if obj.structure_url is not None:
            el.set("structureURL", obj.structure_url)
        is_partial: bool = getattr(obj, "is_partial", False)
        if is_partial:
            el.set("isPartial", "true")

    def urn_to_ref_el(self, urn: SdmxUrn[object]) -> etree._Element:
        """Build a Ref element carrying *urn* as agencyID/id/version attrs.

        SDMX-ML Ref attributes differ for maintainable vs. item refs (per
        SDMXCommonReferences.xsd):

          - Maintainable Ref: ``agencyID``, ``id``, ``version``, ``class``,
            ``package``.
          - Item Ref: ``agencyID``, ``id`` (the item's id),
            ``maintainableParentID`` (the scheme's id),
            ``maintainableParentVersion``, ``class``, ``package``.
            Item Refs must not carry a ``version`` attribute.
        """
        ref = etree.Element("Ref")
        ref.set("agencyID", urn.agency)
        if urn.item_id is not None and self.grammar == "2.1" and urn.artefact_class == "HierarchicalCode":
            # 2.1's node reference names the hierarchy *and* the HCL around it
            # (`_HCL_OF`). A hierarchy no declared HCL claims is written inside
            # a synthesised one carrying its own id, so that is the fallback.
            ref.set("id", urn.item_id)
            hierarchy = str(attrs.evolve(urn, artefact_class="Hierarchy", item_id=None))
            hcl_id = (_HCL_OF.get() or {}).get(hierarchy, urn.id)
            ref.set("maintainableParentID", hcl_id)
            # The id the hierarchy is written with inside that HCL (#421).
            ref.set("containerID", inner_id(urn.id, hcl_id) or urn.id)
            if urn.version != "latest":
                ref.set("maintainableParentVersion", urn.version)
        elif urn.item_id is not None:
            ref.set("id", urn.item_id)
            ref.set("maintainableParentID", urn.id)
            # Omitted for "latest", as `version` is below — never a literal
            # the caller did not give (it wrote "1.0" until #429). The schema
            # reads the omission as 1.0, as the reader does (#476), so
            # `serialize` refuses such a reference before it gets here.
            if urn.version != "latest":
                ref.set("maintainableParentVersion", urn.version)
        else:
            ref.set("id", urn.id)
            if urn.version != "latest":
                ref.set("version", urn.version)
        ref.set("class", urn.artefact_class)
        ref.set("package", urn.package)
        return ref

    def write_selection(
        self,
        parent: etree._Element,
        qualify: Callable[[str], str],
        tag: str,
        selection: KeyValue | ComponentValueSet,
    ) -> None:
        """Append one cube-region selection: its values, or its time range.

        `qualify` picks the namespace — 2.1 writes constraint content in
        `com:`, 3.0 and 3.1 in `str:` — and `tag` the element: `KeyValue`, or
        a component's `Attribute` (2.1) / `Component` (3.x). `TimeRangeValueType`
        is a choice of `BeforePeriod`, `AfterPeriod` or `StartPeriod` +
        `EndPeriod`, so which a range writes follows from which ends it has.
        """
        el = etree.SubElement(parent, qualify(tag))
        el.set("id", selection.dimension_id if isinstance(selection, KeyValue) else selection.component_id)
        if not selection.include:
            el.set("include", "false")
        # 3.x's additions (#422): 2.1 has none of them, and `gaps.py` says so.
        v3 = self.grammar != "2.1"
        if v3 and selection.remove_prefix:
            el.set("removePrefix", "true")
        if v3 and isinstance(selection, KeyValue):
            _set_validity(el, selection.valid_from, selection.valid_to)
        time_range = selection.time_range
        if time_range is None:
            for value in sorted(selection.values, key=_value_order):
                value_el = etree.SubElement(el, qualify("Value"))
                value_el.text = value.value
                self._write_value_qualifiers(value_el, value, v3=v3)
            return
        _write_time_range(etree.SubElement(el, qualify("TimeRange")), time_range, qualify, v3=v3)

    @staticmethod
    def _write_value_qualifiers(value_el: etree._Element, value: RegionValue, *, v3: bool) -> None:
        """A region value's qualifiers, as far as the version has them.

        2.1's `cascadeValues` is a boolean, so `EXCLUDE_ROOT` has no 2.1
        spelling; `check_serialisable` refuses it before a write gets here.
        """
        cascade = value.cascade
        # The code alone is every format's default (#503).
        if cascade is not CascadeSelection.SELF_ONLY and (v3 or cascade is not CascadeSelection.EXCLUDE_ROOT):
            value_el.set("cascadeValues", cascade.value)
        if v3:
            if value.lang is not None:
                value_el.set(XML_LANG, value.lang)
            _set_validity(value_el, value.valid_from, value.valid_to)

    def write_ref(self, parent: etree._Element, ref: Ref[object]) -> None:
        """Append a Ref child carrying the reference URN."""
        parent.append(self.urn_to_ref_el(ref.urn))

    def write_constraint_attachment(self, parent: etree._Element, attachment: ConstraintAttachment) -> None:
        """Append a ``<str:ConstraintAttachment>``, every alternative it holds, in schema order (#422).

        ``DataProvider``, ``DataSet``, ``SimpleDataSource``, the structures,
        flows or agreements, then ``QueryableDataSource`` — the order of
        `ConstraintAttachmentType` in 2.1 and 3.0. Which alternatives a version
        accepts is `check_serialisable`'s question, asked before any bytes.

        ConstraintAttachment is minOccurs="0" in every version, and the type
        requires one of its target elements when present — so an attachment
        with no target is *omitted*, not emitted empty. Emitting the empty
        wrapper produced a document the schema rejects (#229).
        """
        if attachment.is_empty:
            return
        v21 = "v2_1" in self.com
        att_el = etree.SubElement(parent, self.s("ConstraintAttachment"))

        def ref_el(container: etree._Element, tag: str, ref: Ref[object]) -> None:
            el = etree.SubElement(container, tag)
            if v21:
                self.write_ref(el, ref)
            else:
                el.text = str(ref.urn)

        if attachment.data_provider is not None:
            ref_el(att_el, self.s("DataProvider"), attachment.data_provider)
        for data_set in attachment.data_sets:
            set_el = etree.SubElement(att_el, self.s("DataSet"))
            ref_el(set_el, self.c("DataProvider"), data_set.data_provider)
            etree.SubElement(set_el, self.c("ID")).text = data_set.id
        for url in attachment.simple_data_sources:
            etree.SubElement(att_el, self.s("SimpleDataSource")).text = url
        for tag, refs in (
            ("DataStructure", attachment.data_structures),
            ("Dataflow", attachment.dataflows),
            ("ProvisionAgreement", attachment.provision_agreements),
        ):
            for ref in refs:
                ref_el(att_el, self.s(tag), ref)
        for source in attachment.queryable_data_sources:
            self._write_queryable_data_source(att_el, source)

    def _write_queryable_data_source(self, parent: etree._Element, source: QueryableDataSource) -> None:
        el = etree.SubElement(parent, self.s("QueryableDataSource"))
        el.set("isRESTDatasource", "true" if source.is_rest_datasource else "false")
        el.set("isWebServiceDatasource", "true" if source.is_web_service_datasource else "false")
        etree.SubElement(el, self.c("DataURL")).text = source.data_url
        if source.wsdl_url is not None:
            etree.SubElement(el, self.c("WSDLURL")).text = source.wsdl_url
        if source.wadl_url is not None:
            etree.SubElement(el, self.c("WADLURL")).text = source.wadl_url

    def write_data_key_sets(self, parent: etree._Element, key_sets: Sequence[DataKeySet]) -> None:
        """Append one ``<str:DataKeySet>`` per set, each with its ``isIncluded`` (#241, #479).

        A constraint may select by explicit key as well as by cube region;
        `DataConstraintType` is ``DataKeySet | CubeRegion`` in 3.0 and 2.1's
        ContentConstraint carries both.
        """
        for key_set in key_sets:
            self._write_data_key_set(parent, key_set)

    def _write_data_key_set(self, parent: etree._Element, key_set: DataKeySet) -> None:
        ks_el = etree.SubElement(parent, self.s("DataKeySet"))
        ks_el.set("isIncluded", "true" if key_set.is_included else "false")
        # 2.1 puts KeyValue/Value in the *common* namespace; 3.0 and 3.1 moved
        # them to *structure*. Emitting the wrong one produces a document the
        # schema rejects — and the reader tolerates both, so only XSD
        # validation catches it.
        v21 = "v2_1" in self.com
        qualify = self.c if v21 else self.s
        for key in key_set.keys:
            key_el = etree.SubElement(ks_el, self.s("Key"))
            if not v21:
                # 2.1's `DataKeyType` has no annotations or validity; 3.x's
                # does (#422).
                if key.valid_from is not None:
                    key_el.set("validFrom", key.valid_from)
                if key.valid_to is not None:
                    key_el.set("validTo", key.valid_to)
                self.write_annotations(key_el, key.annotations)
            for dimension_id, value in key.values:
                kv_el = etree.SubElement(key_el, qualify("KeyValue"))
                kv_el.set("id", dimension_id)
                etree.SubElement(kv_el, qualify("Value")).text = value
            if not v21:
                # After the key values, as `DataKeyType` sequences them (#422).
                for component in key.components:
                    self.write_selection(key_el, self.s, "Component", component)

    def write_text_format(self, parent: etree._Element, tf: Facet, tag: str = "TextFormat") -> None:
        """Append a str:TextFormat element.

        Every `Facet` field maps to exactly one XSD attribute, so the mapping
        is written as a table rather than as a chain of ``if`` statements.
        A field added to `Facet` and forgotten here is silent data loss on
        write that XSD validation cannot catch — the table keeps both halves
        of each pair on one line. Insertion order is the emission order.
        """
        el = etree.SubElement(parent, self.s(tag))
        # Its context's defaults are left out (#503): the reader fills them in.
        kind = text_kind(self.target, tag, parent)
        stated_type = None if tf.type == text_type_default(self.target, tag, parent) else tf.type
        multilingual = kind == "basic" and tf.is_multilingual != MULTILINGUAL_DEFAULT[self.target]
        attrs_out: dict[str, str | None] = {
            "textType": stated_type,
            "maxLength": xsd_num(tf.max_length),
            "minLength": xsd_num(tf.min_length),
            "maxValue": xsd_num(tf.max_value),
            "minValue": xsd_num(tf.min_value),
            "decimals": xsd_num(tf.decimals),
            "pattern": tf.pattern,
            "startValue": xsd_num(tf.start_value),
            "endValue": xsd_num(tf.end_value),
            "isSequence": _xsd_bool(value=tf.is_sequence),
            "isMultiLingual": _xsd_bool(value=tf.is_multilingual) if multilingual else None,
            "interval": xsd_num(tf.interval),
            "timeInterval": tf.time_interval,
            "startTime": tf.start_time,
            "endTime": tf.end_time,
        }
        for name in _PROHIBITED_ON.get(tag, ()):
            attrs_out.pop(name, None)
        for name, value in attrs_out.items():
            if value is not None:
                el.set(name, value)
        # A text format's alone, and SDMX 3.x's: 2.1 has no such element, and
        # `validation` refuses one on an enumeration or coding format (#422).
        if self.grammar != "2.1":
            for sentinel in tf.sentinel_values:
                sentinel_el = etree.SubElement(el, self.s("SentinelValue"))
                sentinel_el.set("value", sentinel.value)
                self.write_istring(sentinel_el, sentinel.name)
                if sentinel.description:
                    self.write_istring(sentinel_el, sentinel.description, "Description")

    def write_representation(
        self,
        parent: etree._Element,
        representation: Ref[object] | Facet | None,
        tag: str = "LocalRepresentation",
        enumeration_format: Facet | None = None,
    ) -> None:
        """Append a str:LocalRepresentation (or str:CoreRepresentation) element.

        ``RepresentationType`` is ``TextFormat | (Enumeration,
        EnumerationFormat?)`` — an enumerated component may carry a *format*
        alongside its codelist, constraining the codes themselves ("a code
        from CL_FREQ, and that code is one character"). sdmxlib modelled only
        one or the other and dropped the format, which the ECB's own data
        structure carries on fourteen components (#237).
        """
        if representation is None and enumeration_format is None:
            return
        rep_el = etree.SubElement(parent, self.s(tag))
        match representation:
            case Facet():
                self.write_text_format(rep_el, representation)
            case Ref():
                enum_el = etree.SubElement(rep_el, self.s("Enumeration"))
                enum_el.append(self.urn_to_ref_el(representation.urn))
            case None:
                pass  # an enumeration format alone, written below
        if enumeration_format is not None:
            self.write_text_format(rep_el, enumeration_format, tag="EnumerationFormat")

    def write_concept_identity(self, parent: etree._Element, ref: Ref[object]) -> None:
        """Append a str:ConceptIdentity with a Ref child."""
        ci = etree.SubElement(parent, self.s("ConceptIdentity"))
        self.write_ref(ci, ref)

    def write_code(self, parent: etree._Element, code: Code) -> None:
        el = etree.SubElement(parent, self._s_code)
        el.set("id", code.id)
        self.write_uri(el, code)
        self.write_annotations(el, code.annotations)
        self.write_istring(el, code.name)
        if code.description:
            self.write_istring(el, code.description, "Description")
        if code.parent_id is not None:
            parent_el = etree.SubElement(el, self._s_parent)
            ref = etree.SubElement(parent_el, "Ref")
            ref.set("id", code.parent_id)

    def write_codelist(self, parent: etree._Element, cl: Codelist) -> None:
        el = etree.SubElement(parent, self.s("Codelist"))
        self.write_maintainable_attrs(el, cl)
        self.write_annotations(el, cl.annotations)
        self.write_istring(el, cl.name)
        if cl.description:
            self.write_istring(el, cl.description, "Description")
        for code in cl.codes:
            self.write_code(el, code)

    def write_concept(self, parent: etree._Element, concept: Concept) -> None:
        el = etree.SubElement(parent, self.s("Concept"))
        el.set("id", concept.id)
        self.write_uri(el, concept)
        self.write_annotations(el, concept.annotations)
        self.write_istring(el, concept.name)
        if concept.description:
            self.write_istring(el, concept.description, "Description")
        # `Parent` belongs to `ConceptBaseType` and `CoreRepresentation` to the
        # extension, so the XSD sequences Parent *first*. Emitting them the
        # other way round produces a document our own reader accepts and every
        # validator rejects — the class of writer bug the XSD round-trip tests
        # exist to catch.
        if concept.parent_id is not None:
            parent_el = etree.SubElement(el, self._s_parent)
            ref = etree.SubElement(parent_el, "Ref")
            ref.set("id", concept.parent_id)
        self.write_representation(
            el,
            concept.core_representation,
            tag="CoreRepresentation",
            enumeration_format=core_enumeration_format(concept),
        )
        self.write_iso_concept_reference(el, concept.iso_concept_reference)

    def write_iso_concept_reference(self, el: etree._Element, iso: ISOConceptReference | None) -> None:
        """Append a concept's ``<str:ISOConceptReference>`` — last in `ConceptType` (#422)."""
        if iso is None:
            return
        iso_el = etree.SubElement(el, self.s("ISOConceptReference"))
        etree.SubElement(iso_el, self.s("ConceptAgency")).text = iso.agency
        etree.SubElement(iso_el, self.s("ConceptSchemeID")).text = iso.concept_scheme_id
        etree.SubElement(iso_el, self.s("ConceptID")).text = iso.concept_id

    def write_concept_scheme(self, parent: etree._Element, cs: ConceptScheme) -> None:
        el = etree.SubElement(parent, self.s("ConceptScheme"))
        self.write_maintainable_attrs(el, cs)
        self.write_annotations(el, cs.annotations)
        self.write_istring(el, cs.name)
        if cs.description:
            self.write_istring(el, cs.description, "Description")
        for concept in cs.concepts:
            self.write_concept(el, concept)

    def write_dataflow(self, parent: etree._Element, df: Dataflow) -> None:
        el = etree.SubElement(parent, self.s("Dataflow"))
        self.write_maintainable_attrs(el, df)
        self.write_annotations(el, df.annotations)
        self.write_istring(el, df.name)
        if df.description:
            self.write_istring(el, df.description, "Description")
        if isinstance(df.structure, Ref):
            struct_el = etree.SubElement(el, self.s("Structure"))
            self.write_ref(struct_el, df.structure)

    def write_category(self, parent: etree._Element, cat: Category) -> None:
        el = etree.SubElement(parent, self.s("Category"))
        el.set("id", cat.id)
        self.write_uri(el, cat)
        self.write_annotations(el, cat.annotations)
        self.write_links(el, cat.links)
        self.write_istring(el, cat.name)
        if cat.description:
            self.write_istring(el, cat.description, "Description")
        for child in cat.categories:
            self.write_category(el, child)

    def write_category_scheme(self, parent: etree._Element, cs: CategoryScheme) -> None:
        el = etree.SubElement(parent, self.s("CategoryScheme"))
        self.write_maintainable_attrs(el, cs)
        self.write_annotations(el, cs.annotations)
        self.write_links(el, cs.links)
        self.write_istring(el, cs.name)
        if cs.description:
            self.write_istring(el, cs.description, "Description")
        for cat in cs.categories:
            self.write_category(el, cat)

    def write_categorisation(self, parent: etree._Element, cat: Categorisation) -> None:
        el = etree.SubElement(parent, self.s("Categorisation"))
        self.write_maintainable_attrs(el, cat)
        self.write_annotations(el, cat.annotations)
        self.write_istring(el, cat.name)
        if cat.description:
            self.write_istring(el, cat.description, "Description")
        if cat.source is not None:
            source_el = etree.SubElement(el, self.s("Source"))
            self.write_ref(source_el, cat.source)
        if cat.target is not None:
            target_el = etree.SubElement(el, self.s("Target"))
            self.write_ref(target_el, cat.target)

    def write_provision_agreement(self, parent: etree._Element, pa: ProvisionAgreement) -> None:
        el = etree.SubElement(parent, self.s("ProvisionAgreement"))
        self.write_maintainable_attrs(el, pa)
        self.write_annotations(el, pa.annotations)
        self.write_istring(el, pa.name)
        if pa.description:
            self.write_istring(el, pa.description, "Description")
        if pa.dataflow is not None:
            su_el = etree.SubElement(el, self.s("StructureUsage"))
            self.write_ref(su_el, pa.dataflow)
        if pa.data_provider is not None:
            dp_el = etree.SubElement(el, self.s("DataProvider"))
            self.write_ref(dp_el, pa.data_provider)

    def _write_id_pair_maps(
        self,
        parent: etree._Element,
        pair_tag: str,
        pairs: Sequence[ItemMap],
    ) -> None:
        """Append <Source><Ref id=.../></Source><Target><Ref id=.../></Target> pairs."""
        for pair in pairs:
            m_el = etree.SubElement(parent, self.s(pair_tag))
            self.write_annotations(m_el, pair.annotations)
            src_el = etree.SubElement(m_el, self.s("Source"))
            etree.SubElement(src_el, "Ref").set("id", pair.source_id)
            tgt_el = etree.SubElement(m_el, self.s("Target"))
            etree.SubElement(tgt_el, "Ref").set("id", pair.target_id)

    def write_scheme_map(
        self,
        parent: etree._Element,
        tag: str,
        sm: SchemeMap,
        pair_tag: str,
        *,
        ends: tuple[Ref[object] | None, Ref[object] | None],
        pairs: Sequence[ItemMap],
    ) -> None:
        """Write a 2.1 scheme map: its `ends` (source, target) and id-to-id `pairs`.

        The caller supplies both because a `RepresentationMap` keeps them in
        its own shape — endpoints and N:M mappings — which only its 1:1 case
        reduces to this one.
        """
        el = etree.SubElement(parent, self.s(tag))
        el.set("id", sm.id)
        self.write_uri(el, sm)
        # Before Name: every one of these derives from `AnnotableType`, which
        # sequences `Annotations` first. Omitted entirely until #355 — a 2.1
        # map's annotations were dropped on write with nothing to notice, since
        # the reader did not look for them either.
        self.write_annotations(el, sm.annotations)
        self.write_istring(el, sm.name)
        if sm.description:
            self.write_istring(el, sm.description, "Description")
        source, target = ends
        if source is not None:
            src_el = etree.SubElement(el, self.s("Source"))
            self.write_ref(src_el, source)
        if target is not None:
            tgt_el = etree.SubElement(el, self.s("Target"))
            self.write_ref(tgt_el, target)
        self._write_id_pair_maps(el, pair_tag, pairs)


#: `xs:boolean`'s lexical space: ``1`` and ``0`` are as valid as the words.
#: Read inline at a dozen sites, four rules had grown up, and ``leveled="1"``
#: read false while ``isInclusive="0"`` read true (#458).
_XS_BOOLEAN = {"true": True, "1": True, "false": False, "0": False}


def optional_flag(el: etree._Element, attribute: str) -> bool | None:
    """An `xs:boolean` attribute with no default: None when absent, or not a boolean."""
    value = el.get(attribute)
    return None if value is None else _XS_BOOLEAN.get(value.strip().lower())


def required_flag(el: etree._Element, attribute: str) -> bool:
    """A required `xs:boolean` attribute: absent is refused, as `required` refuses it."""
    return _XS_BOOLEAN.get(required(el, attribute).strip().lower(), False)


def node_level(levels_by_id: Mapping[str, Level], level_id: str) -> Level | None:
    """The level a hierarchical code names, or None when its hierarchy defines no such level (#501).

    A reference to nothing cannot be held, so the node is read with no level.
    It is not dropped silently, which is what both readers did before #458:
    `_losses.LOSSES` counts each such reference, so the read records it on
    `StructureMessage.lost` and `lossless=True` refuses it. Refusing the
    hierarchy instead (#458) cost the whole tree for one reference per node.
    IMF's 99 hierarchies name levels `0` to `5` and define none.
    """
    return levels_by_id.get(level_id)


def flag(el: etree._Element, attribute: str, *, default: bool = False) -> bool:
    """An `xs:boolean` attribute, `default` when absent — the schema's default, false unless it says otherwise."""
    value = optional_flag(el, attribute)
    return default if value is None else value


def _set_validity(el: etree._Element, valid_from: str | None, valid_to: str | None) -> None:
    if valid_from is not None:
        el.set("validFrom", valid_from)
    if valid_to is not None:
        el.set("validTo", valid_to)


def _value_order(value: RegionValue) -> tuple[str, str]:
    """Document order for a value set: by value, then language."""
    return value.value, value.lang or ""
