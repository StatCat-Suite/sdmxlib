"""SDMX structural mapping types — 3.0-native model.

Each map kind is a top-level MaintainableArtefact in this model, matching
SDMX 3.0's design where the unified 2.1 ``StructureSet`` wrapper was
dropped and each map kind became its own container.

The 2.1 serializer nests them inside ``<str:StructureSet>``. That container
is itself a maintainable in 2.1 — its own id, agency, version, validity
dates and name — so it is modelled here as `StructureSet` rather than
synthesised, and the maps it groups stay top-level artefacts referenced from
it. Maps belonging to no declared set still get a synthetic wrapper on write,
since 2.1 has nowhere else to put them. The 3.0
serializer emits each kind to its own top-level container per v3.0.0
``SDMXStructureStructureMappings.xsd``:

============================  ================================
Model type                    SDMX-ML 3.0 container
============================  ================================
``RepresentationMap``         ``<str:RepresentationMaps>``
``StructureMap``              ``<str:StructureMaps>``
``ConceptSchemeMap``          ``<str:ConceptSchemeMaps>``
``CategorySchemeMap``         ``<str:CategorySchemeMaps>``
============================  ================================

The 2.1 dispatch on write:

- ``RepresentationMap`` (codelist-to-codelist case) → ``<str:CodelistMap>``
- ``StructureMap`` → ``<str:StructureMap>``, whatever its source artefact is;
  ``StructureSetType`` names one such child and the Source/Target references
  say what is being mapped
- ``ConceptSchemeMap`` → ``<str:ConceptSchemeMap>`` (inner ``<ConceptMap>``)
- ``CategorySchemeMap`` → ``<str:CategorySchemeMap>`` (inner ``<CategoryMap>``)

2.1 references a mapped *component* by class and component-list id as well as
by id, and neither is on the map — see `component_location`, which reads them
off the structure the map names.
"""

from collections.abc import Iterable
from datetime import date, datetime
from enum import Enum
from typing import ClassVar

from attrs import field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, artefact_urn, refuse_urn_text, to_optional_ref, to_refs
from sdmxlib.model.representation import Facet, to_text_format
from sdmxlib.model.urn import ParsesUrn, SdmxUrn


@frozen(unsafe_hash=False)
class ItemMap:
    """Single item-level mapping: source item id → target item id.

    Used by `ConceptSchemeMap`, `CategorySchemeMap`. Matches the SDMX
    3.0 ``SingleValueMappingType`` (id-to-id, with its validity dates; the
    1:N variants are not modelled).
    """

    source_id: str
    target_id: str
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX 3.x ``@validFrom`` / ``@validTo``, ``xs:date`` as a value map's (#422).
    valid_from: date | None = None
    valid_to: date | None = None


# ── Representation mappings (SDMX 3.x ``RepresentationMapping``) ─────────────
# The XSD's ``ValueMappingType`` holds one or more ``SourceValue``s
# (``MappedValueType``) and zero or more ``TargetValue``s, so a mapping is
# N:M (#422). Each source value says for itself how it matches: its optional
# ``isRegEx`` / ``startIndex`` / ``endIndex`` attributes make it a literal, a
# regular expression (the targets may use ``\1`` style backreferences), or a
# substring. The flags live on the `SourceValue`, then, not on the mapping:
# one mapping may match a literal on one source and a pattern on the next.


def _to_texts[T](values: Iterable[T]) -> tuple[T, ...]:
    """Ids or values as a tuple, refusing one bare string.

    A `str` is itself an iterable of strings, so ``targets="A"`` would
    otherwise be read as ``("A",)``'s characters — the defect #261 shipped.
    """
    if isinstance(values, str):
        msg = f"expected a tuple of strings, got the string {values!r}; write ({values!r},)"
        raise TypeError(msg)
    return tuple(values)


@frozen
class SourceValue:
    """One source value of a `RepresentationMapping` — SDMX's ``MappedValueType``.

    A literal unless `is_regex` makes `value` a regular expression, or
    `start_index` / `end_index` take the substring it is compared with. The
    indexes are the wire's integers, passed through without reindexing.
    """

    value: str
    is_regex: bool = False
    start_index: int | None = None
    end_index: int | None = None

    @property
    def is_literal(self) -> bool:
        """Whether `value` is compared whole and as written."""
        return not self.is_regex and self.start_index is None and self.end_index is None


def _to_source_values(values: Iterable[SourceValue | str]) -> tuple[SourceValue, ...]:
    """Source values as a tuple — a plain string is a literal `SourceValue`."""
    return tuple(v if isinstance(v, SourceValue) else SourceValue(v) for v in _to_texts(values))


@frozen(unsafe_hash=False)
class RepresentationMapping:
    """One mapping of a `RepresentationMap`: source values → target values.

    SDMX 3.x's ``RepresentationMapping`` (``ValueMappingType``). It is N:M:
    one source value per source endpoint of the map, one target value per
    target endpoint. No target maps the source values to nothing.
    """

    sources: tuple[SourceValue, ...] = field(converter=_to_source_values)
    targets: tuple[str, ...] = field(factory=tuple, converter=_to_texts)
    #: ``xs:date`` on the wire, not ``xs:dateTime``.
    valid_from: date | None = None
    valid_to: date | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)


@frozen
class MapDataType:
    """A primitive data type endpoint for a ``RepresentationMap``.

    Alternative to a codelist reference (#131). The endpoint is a
    primitive SDMX ``DataType`` literal — ``"String"``, ``"Integer"``,
    ``"Decimal"``, ``"Boolean"``, etc. — rather than a codelist of
    enumerated values.

    Use case: mapping representation between a coded value and a
    primitive type (e.g. codelist codes to integer indices, or one
    text format to another).

    Example:
        rm = RepresentationMap(
            id="LEGACY_TO_INT",
            sources=(Ref[Codelist].unresolved(cl_urn),),
            targets=(MapDataType("Integer"),),
            mappings=(RepresentationMapping(sources=("A",), targets=("1",)),),
        )
    """

    data_type: str


#: Discriminated union of a ``RepresentationMap`` source/target endpoint.
#:
#: Either a codelist ``Ref`` (the original 2.1 / 3.0 case) or a
#: ``MapDataType`` literal (the 3.0 primitive-type case added in #131).
#: Callers pattern-match on the variant to decide how to interpret it:
#:
#: .. code-block:: python
#:
#:     match rm.sources[0]:
#:         case Ref(urn=cl_urn):
#:             ...
#:         case MapDataType(data_type=dt):
#:             ...
MapEndpoint = Ref[object] | MapDataType


def _to_map_sides(sides: Iterable[MapEndpoint | SdmxUrn[object]]) -> tuple[MapEndpoint, ...]:
    """`to_refs` for a map's endpoints, each of which may also be a `MapDataType` (#418)."""
    return tuple(Ref[object].unresolved(v) if isinstance(v, SdmxUrn) else v for v in sides)


@frozen(unsafe_hash=False)
class RepresentationMap(MaintainableArtefact):
    """Maps representations to others.

    Supersedes the 2.1 ``CodelistMap``. Each endpoint is either a codelist
    ``Ref`` or a ``MapDataType`` literal, and there may be several on
    each side: a mapping then holds one source value per source and one
    target value per target. The 2.1 serializer round-trips the one-codelist-
    to-one-codelist case as ``<str:CodelistMap>`` with ``<str:CodeMap>``
    entries.

    The 3.x-only shapes — several endpoints, a dataType endpoint, and regex,
    index or N:M mappings — have no 2.1 form and are refused there rather
    than dropped. They used to
    be written as plain literal pairs, which restated a regex pattern as a
    code id: a document asserting a mapping the source never made.
    """

    #: ``structuremapping``, not ``structuremap``: the latter is in no SDMX
    #: vocabulary. 3.0 and 3.1 spell it ``structuremapping`` (and the JSON
    #: schema's URN pattern enumerates it), 2.1 spells the package
    #: ``mapping``; the model carries the 3.0 grammar, as it does for the
    #: renamed component classes (#404). Nothing pinned the old value —
    #: zero fixtures, zero tests — because nothing put it anywhere a schema
    #: could read it until the JSON writer emitted a self-URN (#406).
    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "RepresentationMap"
    msg_field: ClassVar[str] = "representation_maps"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    sources: tuple[MapEndpoint, ...] = field(factory=tuple, converter=_to_map_sides, validator=refuse_urn_text)
    targets: tuple[MapEndpoint, ...] = field(factory=tuple, converter=_to_map_sides, validator=refuse_urn_text)
    mappings: tuple[RepresentationMapping, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)

    def translate(self, source_value: str) -> str | None:
        """Return the target value for a source value, or None if unmapped.

        Only reads 1:1 mappings whose source is a literal — a regex, index or
        N:M mapping needs the caller to apply its semantics themselves.
        """
        for m in self.mappings:
            match m.sources, m.targets:
                case (SourceValue(value=value) as source,), (target,) if source.is_literal and value == source_value:
                    return target
                case _:
                    pass
        return None


@frozen(unsafe_hash=False)
class ConceptSchemeMap(MaintainableArtefact):
    """Maps one concept scheme to another, concept by concept."""

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "ConceptSchemeMap"
    msg_field: ClassVar[str] = "concept_scheme_maps"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    source: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    target: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    maps: tuple[ItemMap, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False


@frozen(unsafe_hash=False)
class CategorySchemeMap(MaintainableArtefact):
    """Maps one category scheme to another, category by category."""

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "CategorySchemeMap"
    msg_field: ClassVar[str] = "category_scheme_maps"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    source: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    target: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    maps: tuple[ItemMap, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False


@frozen(unsafe_hash=False)
class OrganisationSchemeMap(MaintainableArtefact):
    """Maps one organisation scheme to another, organisation by organisation.

    Its ends may be any organisation scheme — agencies, data or metadata
    providers, or the data consumer and organisation unit schemes sdmxlib
    does not model, whose references then stay unresolved. SDMX calls each
    pair an ``OrganisationMap``; it is an `ItemMap` as for the other two
    scheme maps.
    """

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "OrganisationSchemeMap"
    msg_field: ClassVar[str] = "organisation_scheme_maps"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    source: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    target: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    maps: tuple[ItemMap, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False


class ToValueType(Enum):
    """What of a source value a 2.1 component map carries into an uncoded target — ``ToValueTypeType``."""

    VALUE = "Value"
    NAME = "Name"
    DESCRIPTION = "Description"


def _to_value_type(value: ToValueType | str | None) -> ToValueType | None:
    return None if value is None else ToValueType(value)


#: The ``<com:AnnotationType>`` on a `RepresentationMap` an SDMX-ML 2.1 reader
#: built from a component map's inline ``<ValueMap>`` (#422). 3.x keeps value
#: pairs only in a maintainable map, so the reader gives the pairs one, named
#: ``<structure map id>_VALUE_MAP_<n>`` after the component map's position; the
#: mark is how a 2.1 write knows to put them back inline rather than refuse a
#: data-type map 2.1 has no standalone form for.
BUILT_VALUE_MAP_TYPE = "sdmxlib:value-map"


@frozen(unsafe_hash=False)
class ComponentMap:
    """Single component-level mapping inside a StructureMap.

    SDMX 3.x lets one map several source components to several target ones;
    2.1's is one to one.
    """

    sources: tuple[str, ...] = field(converter=_to_texts)
    targets: tuple[str, ...] = field(converter=_to_texts)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: How the values are mapped (SDMX 3.x ``<RepresentationMap>``, #422): a
    #: `RepresentationMap`, or one of the structure map's own `EpochMap` /
    #: `DatePatternMap`s, by URN. None maps the values unchanged.
    #:
    #: SDMX-ML 2.1 says this inside the component map, as a
    #: ``<RepresentationMapping>``: a ``<CodelistMap>`` names a map in the same
    #: structure set, read as that map's URN; a ``<ValueMap>`` lists the pairs
    #: inline, read as a data-type `RepresentationMap` built for it and marked
    #: `BUILT_VALUE_MAP_TYPE` — the one 3.x form of value pairs — which a 2.1
    #: write puts back inline.
    representation_map: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    #: SDMX-ML 2.1's third ``<RepresentationMapping>``: the uncoded format the
    #: target takes (``<ToTextFormat>``), always with `to_value_type`. SDMX 3.x
    #: has no counterpart, so a 3.x or SDMX-JSON write drops it (declared in
    #: `formats/gaps.py`, #422).
    to_text_format: Facet | None = field(default=None, converter=to_text_format)
    #: What of the source value the target takes, with `to_text_format`.
    to_value_type: ToValueType | None = field(default=None, converter=_to_value_type)


class EpochPeriod(Enum):
    """The unit an `EpochMap` counts in — SDMX's ``EpochPeriodType``."""

    NANOSECOND = "nanosecond"
    MILLISECOND = "millisecond"
    MICROSECOND = "microsecond"
    SECOND = "second"
    DAY = "day"


class ResolvePeriod(Enum):
    """Where in a lower-frequency period a date map resolves — SDMX's ``ResolvePeriodType``."""

    START_OF_PERIOD = "startOfPeriod"
    END_OF_PERIOD = "endOfPeriod"
    MID_PERIOD = "midPeriod"


def _to_id_pairs(pairs: Iterable[Iterable[str]]) -> tuple[tuple[str, str], ...]:
    """Each (source, target) pair as a tuple — lists from a reader included."""
    out: list[tuple[str, str]] = []
    for pair in pairs:
        source, target = pair
        out.append((source, target))
    return tuple(out)


def _to_resolve_period(value: ResolvePeriod | str | None) -> ResolvePeriod | None:
    return None if value is None else ResolvePeriod(value)


@frozen(unsafe_hash=False)
class EpochMap(ParsesUrn):
    """Maps an epoch count to a time period — SDMX 3.x's ``EpochMap``.

    The source components hold a number of `epoch_period`s since
    `base_period`; the target, a time period. Frequency comes from a
    `frequency_dimension` (whose codes the `mapped_frequencies` —
    `FrequencyFormatMapping` ids — say how to format), or is the one
    `target_frequency_id`: the schema's choice, so exactly one of the two.
    """

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "EpochMap"

    id: str
    #: The (source id, target id) component pairs the map applies to.
    component_maps: tuple[tuple[str, str], ...] = field(factory=tuple, converter=_to_id_pairs)
    base_period: str = ""
    epoch_period: EpochPeriod = field(default=EpochPeriod.DAY, converter=EpochPeriod)
    frequency_dimension: str | None = None
    mapped_frequencies: tuple[str, ...] = field(factory=tuple, converter=tuple)
    target_frequency_id: str | None = None
    resolve_period: ResolvePeriod | None = field(default=None, converter=_to_resolve_period)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


@frozen(unsafe_hash=False)
class DatePatternMap(ParsesUrn):
    """Maps a formatted date to a time period — SDMX 3.x's ``DatePatternMap``.

    The source components hold dates written in `source_pattern` for
    `locale`. Its other fields are an `EpochMap`'s, which shares the schema's
    ``DateMapType``.
    """

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "DatePatternMap"

    id: str
    component_maps: tuple[tuple[str, str], ...] = field(factory=tuple, converter=_to_id_pairs)
    source_pattern: str = ""
    locale: str = ""
    frequency_dimension: str | None = None
    mapped_frequencies: tuple[str, ...] = field(factory=tuple, converter=tuple)
    target_frequency_id: str | None = None
    resolve_period: ResolvePeriod | None = field(default=None, converter=_to_resolve_period)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


@frozen(unsafe_hash=False)
class FrequencyFormatMapping(ParsesUrn):
    """How a frequency's periods are written — SDMX 3.x's ``FrequencyFormatMapping``.

    A date map's `mapped_frequencies` name these by id.
    """

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "FrequencyFormatMapping"

    id: str
    frequency_id: str = ""
    date_pattern: str = ""
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


@frozen(unsafe_hash=False)
class FixedValueMap:
    """A fixed value for one source or target component — SDMX 3.x's ``FixedValueMap``.

    The schema's choice of ``Source`` or ``Target``, so exactly one of
    `source_id` / `target_id`.
    """

    source_id: str | None = None
    target_id: str | None = None
    values: tuple[str, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)


@frozen
class ComponentLocation(ParsesUrn):
    """Where a component sits inside its structure, in information-model terms.

    A `ComponentMap` names its endpoints by id alone, which is all SDMX 3.0
    carries. SDMX 2.1 references them *locally*: the id, the component's own
    object class, and the id of the ComponentList holding it — all three
    required by ``LocalComponentListComponentRefType``. The extra two are not
    information 3.0 dropped; they are recoverable from the structure the map
    names, which is what `component_location` does.

    Attributes:
        sdmx_class: The component's object class — ``"Dimension"``,
            ``"TimeDimension"``, ``"MeasureDimension"``, ``"Attribute"`` or
            ``"PrimaryMeasure"``.
        descriptor_id: The id of the ComponentList holding it. Fixed by the
            information model per kind, and by the 2.1 XSD as the ``@id`` of
            ``<str:DimensionList>``, ``<str:AttributeList>`` and
            ``<str:MeasureList>``.
    """

    sdmx_class: str
    descriptor_id: str


#: Component kind -> its `ComponentLocation`. `Measure` is ``PrimaryMeasure``
#: because 2.1 is the only format that asks: it allows exactly one measure and
#: names its class accordingly, where 3.0 allows many and calls them Measures.
#: A 3.0 structure with several measures cannot be written as 2.1 at all, so
#: the single-measure reading is the only one this table is ever used under.
_COMPONENT_LOCATIONS: dict[str, ComponentLocation] = {
    "Dimension": ComponentLocation("Dimension", "DimensionDescriptor"),
    "TimeDimension": ComponentLocation("TimeDimension", "DimensionDescriptor"),
    "MeasureDimension": ComponentLocation("MeasureDimension", "DimensionDescriptor"),
    "Attribute": ComponentLocation("Attribute", "AttributeDescriptor"),
    "Measure": ComponentLocation("PrimaryMeasure", "MeasureDescriptor"),
}


def mapped_structure(
    ref: Ref[object] | None,
    data_structures: Iterable[DataStructure],
    dataflows: Iterable[Dataflow],
) -> DataStructure | None:
    """The `DataStructure` a `StructureMap` endpoint names, if it is reachable.

    A structure map's source and target are ``StructureOrUsageReference`` —
    either a DataStructure or a Dataflow standing in for the structure it
    publishes. Either way the components being mapped belong to one
    DataStructure, and this finds it: through the ref's resolved object when
    the ref carries one, otherwise by URN among the artefacts supplied.

    Args:
        ref: The map's ``source`` or ``target``.
        data_structures: Structures available to resolve against.
        dataflows: Dataflows available to resolve against.

    Returns:
        The structure, or None when the ref is absent, names neither kind, or
        names an artefact not among those supplied — the case where a caller
        knows the component ids but nothing about the components.
    """
    if ref is None:
        return None
    by_urn: dict[str, DataStructure | Dataflow] = {str(artefact_urn(a)): a for a in (*data_structures, *dataflows)}
    named: object = ref() if ref.resolved else by_urn.get(str(ref.urn))
    if isinstance(named, Dataflow):
        named = mapped_structure(named.structure, data_structures, ())
    return named if isinstance(named, DataStructure) else None


def component_location(dsd: DataStructure, component_id: str) -> ComponentLocation | None:
    """Locate a component of `dsd` by id, or None if it has no such component.

    Args:
        dsd: The structure whose components are searched.
        component_id: The component id to find.

    Returns:
        Where that component sits, or None when the structure does not
        define it — which for a `ComponentMap` endpoint means the map names
        a component its structure does not have.
    """
    component = dsd.find_component(component_id)
    if component is None:
        return None
    return _COMPONENT_LOCATIONS[type(component).__name__]


@frozen(unsafe_hash=False)
class StructureMap(MaintainableArtefact):
    """Maps components of one structure to components of another.

    Supersedes the 2.1 ``DataStructureMap`` and ``DataflowMap``. Source and
    target may be either ``DataStructure`` or ``Dataflow`` refs (3.0's
    ``StructureOrUsageReferenceType``). The 2.1 serializer dispatches on
    the source ref class to emit either ``<str:DataStructureMap>`` or
    ``<str:DataflowMap>``.
    """

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "StructureMap"
    msg_field: ClassVar[str] = "structure_maps"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    source: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    target: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    maps: tuple[ComponentMap, ...] = field(factory=tuple, converter=tuple)
    #: SDMX 3.x's date and value maps (#422), each in the wire's own list.
    epoch_maps: tuple[EpochMap, ...] = field(factory=tuple, converter=tuple)
    date_pattern_maps: tuple[DatePatternMap, ...] = field(factory=tuple, converter=tuple)
    frequency_format_mappings: tuple[FrequencyFormatMapping, ...] = field(factory=tuple, converter=tuple)
    fixed_value_maps: tuple[FixedValueMap, ...] = field(factory=tuple, converter=tuple)
    #: SDMX-ML 2.1's ``@isExtension``: the target structure extends the source
    #: (#422). 3.x has no such attribute.
    is_extension: bool = False
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


#: The scheme-map family: a maintainable that maps one scheme onto another.
#: All share ``id``/``name``/``description``/``source``/``target``/``maps``,
#: differing only in the member types of ``source``/``target`` and ``maps`` — so
#: the writers take this union rather than a Protocol, keeping the concrete
#: member types recoverable at each branch.
type SchemeMap = RepresentationMap | ConceptSchemeMap | CategorySchemeMap | OrganisationSchemeMap
#: The subset SDMX 3.0 writes through ``ItemSchemeMapType`` (no RepresentationMap).
type ItemSchemeMap = ConceptSchemeMap | CategorySchemeMap | OrganisationSchemeMap


@frozen(unsafe_hash=False)
class StructureSet(MaintainableArtefact):
    """A 2.1 container grouping related maps.

    SDMX-ML 2.1 nests every map inside a ``<str:StructureSet>``, which is a
    maintainable in its own right: it has an id, an agency, a version, validity
    dates and a name, and it asserts that the maps inside it belong together.
    SDMX 3.0 removed it and promoted each map kind to a top-level container.

    sdmxlib follows 3.0 for the maps themselves — they remain top-level
    artefacts — and models the container as a peer that *references* them.
    That keeps one copy of the grouping rather than denormalising the
    container's identity onto each map, where nothing would stop the copies
    disagreeing.

    Written only to SDMX-ML 2.1. The 3.0, 3.1 and SDMX-JSON targets have no
    such concept, so they leave it out and write its maps
    (`formats.gaps.dropped`); a 2.1 reader names each map for it
    (`model._container.member_id`).

    Example:
        sl.StructureSet(
            id="SS_TEST", maintainer="TEST", version="1.0",
            name={"en": "Test Structure Set"},
            maps=[sl.Ref.to(codelist_map)],
        )
    """

    sdmx_package: ClassVar[str] = "structuremapping"
    sdmx_class: ClassVar[str] = "StructureSet"
    msg_field: ClassVar[str] = "structure_sets"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    #: References to the maps this set groups, in document order. Held as refs
    #: rather than by value so the maps stay single-sourced at the top level.
    maps: tuple[Ref[object], ...] = field(factory=tuple, converter=to_refs, validator=refuse_urn_text)
    #: ``<str:RelatedStructure>`` — structures the set relates without mapping.
    related_structures: tuple[Ref[object], ...] = field(factory=tuple, converter=to_refs, validator=refuse_urn_text)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False


def is_built_value_map(m: object) -> bool:
    """A `RepresentationMap` an SDMX-ML 2.1 reader built from an inline ``<ValueMap>`` (#422).

    Marked `BUILT_VALUE_MAP_TYPE`, data type to data type, each mapping one
    literal value to one: what a 2.1 write can put back inline, and nothing
    else, so a map that lost its shape since is not mistaken for one.
    """
    return (
        isinstance(m, RepresentationMap)
        and BUILT_VALUE_MAP_TYPE in m.annotations.types
        and all(isinstance(end, MapDataType) for end in (*m.sources, *m.targets))
        and len(m.sources) == len(m.targets) == 1
        and all(
            len(mapping.sources) == len(mapping.targets) == 1 and mapping.sources[0].is_literal
            for mapping in m.mappings
        )
    )
