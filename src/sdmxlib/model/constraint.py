"""DataConstraint, CubeRegion, and supporting types."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import Enum
from typing import ClassVar

from attrs import Attribute, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import CascadeSelection
from sdmxlib.model.errors import ValidationError, ValidationIssue
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text, to_optional_ref, to_ref, to_refs


class ConstraintType(Enum):
    """What kind of statement a DataConstraint makes.

    Per ``ContentConstraintTypeCodeType`` (2.1) / ``ConstraintRoleType``
    (3.0 / 3.1). The 2.1 element ``<str:ContentConstraint type=...>``
    maps to the same two values as the 3.0+ ``<str:DataConstraint role=...>``.
    """

    #: Defines what content is **allowed** to be reported. Useful for
    #: building partial codelists or validating incoming data.
    ALLOWED = "Allowed"
    #: Describes what content is **actually present** in the data source.
    #: Useful for advertising data availability.
    ACTUAL = "Actual"

    @classmethod
    def parse(cls, value: str | ConstraintType | None) -> ConstraintType:
        """Parse an SDMX constraint-type string into the enum.

        Returns ``ALLOWED`` for ``None`` (matches the historical default
        when a constraint omits the attribute). An already-typed value
        passes through.
        """
        if value is None:
            return cls.ALLOWED
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError as e:
            msg = f"Unrecognised constraint type {value!r}; expected Allowed or Actual."
            raise ValueError(msg) from e


def _to_key_pairs(v: Iterable[Sequence[str]]) -> tuple[tuple[str, str], ...]:
    """Attrs converter for `DataKey.values` — a tuple *of tuples*.

    A plain ``converter=tuple`` fixes the outer sequence and leaves the pairs
    as whatever was passed, so ``DataKey(values=[["FREQ", "A"]])`` held a list
    inside a tuple and the annotation was still false one level down (#371).
    The only nested declaration in the model, which is why it is the only
    field needing more than the builtin.
    """
    return tuple((pair[0], pair[1]) for pair in v)


@frozen
class TimeBound:
    """One end of a `TimeRange`: an observational time period, as written.

    `period` is the wire text, not a `datetime`: SDMX types it
    `ObservationalTimePeriodType`, which takes reporting periods (`2020-Q1`,
    `2020-A1`) that have no faithful `datetime`, and parsing would also
    decide a time zone the source never gave.
    """

    period: str
    inclusive: bool = True


def _bounded(time_range: TimeRange, _attribute: Attribute[TimeBound | None], end: TimeBound | None) -> None:
    """Refuse a `TimeRange` with neither bound (#492).

    On `end`, since attrs runs a field's validator once every field is set, so
    `start` is there to read. A field validator and not `__attrs_post_init__`,
    so a test can still build one inside `tests/support/unchecked.py`.

    Raises:
        ValidationError: If the range has neither a start nor an end.
    """
    if time_range.start is None and end is None:
        issue = ValidationIssue(path="TimeRange", message="a time range needs a start, an end, or both")
        raise ValidationError([issue])


@frozen
class TimeRange:
    """A time selection in a cube region: before, after, or between periods.

    SDMX's `TimeRangeValueType` is a choice of `BeforePeriod`, `AfterPeriod`,
    or `StartPeriod` *and* `EndPeriod`, so the three shapes map one to one:
    only `end` is "before", only `start` is "after", both is a range. One
    with neither has no form in any format and is refused as it is built.
    """

    start: TimeBound | None = None
    end: TimeBound | None = field(default=None, validator=_bounded)
    #: When the selection starts to apply, as written (`StandardTimePeriodType`)
    #: — SDMX 3.x's ``TimeRange/@validFrom`` (#422). 2.1 has no such attribute.
    valid_from: str | None = None
    #: When the selection stops applying, likewise.
    valid_to: str | None = None


def _cascade(value: CascadeSelection | None) -> CascadeSelection:
    """An unstated cascade is the code alone, every format's default (#503)."""
    return CascadeSelection.SELF_ONLY if value is None else value


@frozen
class RegionValue:
    """One value a cube region's value set lists, with what qualifies it.

    SDMX-ML 3.x's `SimpleComponentValueType` (2.1's `SimpleValueType`), and
    SDMX-JSON's object form of a region value. A bare string is still accepted
    wherever a value set is built, and stands for a `RegionValue` with nothing
    set — the common case, and what every qualifier defaults to.

    Attributes:
        value: The value — usually a code id.
        cascade: Whether the code's descendants are selected too: by default
            the code alone, the `false` every format defaults to.
            2.1 has `true` / `false` only.
        lang: The language of a multilingual text value. SDMX 3.x prohibits
            it on a dimension's value, so only a component's may carry one.
        valid_from: When the value starts to apply, as written
            (`StandardTimePeriodType`, so `2020-Q1` too). 3.x prohibits it on
            a component's value, so only a dimension's may carry one.
        valid_to: When the value stops applying, likewise.
    """

    value: str
    cascade: CascadeSelection = field(default=CascadeSelection.SELF_ONLY, converter=_cascade)
    lang: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


def _to_region_values(values: Iterable[str | RegionValue]) -> frozenset[RegionValue]:
    return frozenset(RegionValue(v) if isinstance(v, str) else v for v in values)


@frozen(unsafe_hash=False)
class KeyValue:
    """A dimension constraint: the allowed values for one dimension, or a time range.

    SDMX's `CubeRegionKeyType` is a choice of `Value+` or one `TimeRange`, so
    a key holds exactly one of the two.
    """

    dimension_id: str
    #: Strings are accepted and become unqualified `RegionValue`s; `codes`
    #: gives the strings back.
    values: frozenset[RegionValue] = field(factory=frozenset, converter=_to_region_values)
    time_range: TimeRange | None = None
    #: Whether the values (or the time range) are the dimension's part of the
    #: region, or excluded from it — SDMX's `@include` on the value set,
    #: default true (#422). Not the region's own `CubeRegion.include`: an
    #: included region may still exclude some of one dimension's values.
    include: bool = True
    #: For a codelist extension: whether a value names the code without the
    #: extension's prefix. SDMX 3.x only (#422).
    remove_prefix: bool = False
    #: When this value set applies, as written (`StandardTimePeriodType`).
    #: SDMX 3.x only, and only on a dimension's set (#422).
    valid_from: str | None = None
    valid_to: str | None = None

    def codes(self) -> frozenset[str]:
        """The values listed, as plain strings."""
        return frozenset(v.value for v in self.values)


@frozen(unsafe_hash=False)
class ComponentValueSet:
    """The values allowed for one non-dimension component in a cube region.

    SDMX-ML 2.1 `<Attribute>`, 3.0 `<Component>`, SDMX-JSON `components`: an
    attribute's or measure's values, or a time range — and, unlike a
    `KeyValue`, possibly neither.
    """

    component_id: str
    #: As `KeyValue.values`.
    values: frozenset[RegionValue] = field(factory=frozenset, converter=_to_region_values)
    time_range: TimeRange | None = None
    #: As `KeyValue.include` (#422).
    include: bool = True
    #: As `KeyValue.remove_prefix`. A component's set has no validity of its
    #: own: SDMX 3.x prohibits it here (#422).
    remove_prefix: bool = False

    def codes(self) -> frozenset[str]:
        """The values listed, as plain strings."""
        return frozenset(v.value for v in self.values)


@frozen(unsafe_hash=False)
class DataKey:
    """A single full or partial data key: one value per dimension.

    Differs from ``KeyValue`` (sets of allowed values per dimension):
    a ``DataKey`` enumerates one concrete key. Dimensions omitted from
    ``values`` are treated as wildcarded per the SDMX 2.1 spec.
    """

    values: tuple[tuple[str, str], ...] = field(factory=tuple, converter=_to_key_pairs)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX 3.x ``@validFrom`` / ``@validTo`` on a key (#422), standard time
    #: periods kept as written, as a `KeyValue`'s are.
    valid_from: str | None = None
    valid_to: str | None = None
    #: SDMX 3.x ``<Component>``: the attribute or measure values the key's
    #: data has, as a cube region's component value sets — the same schema
    #: type (`DataComponentValueSetType`), so the same class (#422).
    components: tuple[ComponentValueSet, ...] = field(factory=tuple, converter=tuple)


@frozen(unsafe_hash=False)
class DataKeySet:
    """Whole observation keys a constraint includes, or excludes.

    SDMX's ``DataKeySet``: ``isIncluded`` is required, and says whether its
    keys are the ones allowed or the ones ruled out. The flag belongs to the
    set, not to a key, so the set is a class of its own, as in the
    information model, pysdmx (`KeySet`) and sdmx1 (`DataKeySet`).

    Example:
        DataKeySet(is_included=False, keys=[DataKey(values=[("FREQ", "A"), ("REF_AREA", "XX")])])
    """

    is_included: bool = True
    keys: tuple[DataKey, ...] = field(factory=tuple, converter=tuple)


@frozen(unsafe_hash=False)
class CubeRegion:
    """A region in the hypercube: per-dimension selections, and per-component ones."""

    include: bool = True
    keys: tuple[KeyValue, ...] = field(factory=tuple, converter=tuple)
    components: tuple[ComponentValueSet, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)

    def allows(self, key: dict[str, str]) -> bool:
        """Return True if *key* is permitted by this region.

        A dimension the key does not mention is not checked. A time range
        constrains only when the key's value and the range's bounds all read
        as ISO dates or date-times; a reporting period (`2020-Q1`) is not
        interpreted, so the range does not exclude it. Components are not
        checked: a key names dimensions.

        A value set with ``include=False`` turns its own test around: the key
        is inside the region on that dimension when its value is *not* one
        of them.

        A value's `RegionValue.cascade` is not expanded — selecting a code's
        descendants needs the codelist, which a key does not carry — and its
        validity, and the set's, are not checked against any date.
        """
        for kv in self.keys:
            if kv.dimension_id not in key:
                continue
            value = key[kv.dimension_id]
            listed = _in_range(value, kv.time_range) if kv.time_range is not None else value in kv.codes()
            inside = listed if kv.include else not listed
            if not inside:
                return not self.include
        return self.include


def _iso(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Compared as wall times: a bound and a key value are one source's
    # periods, and mixing an aware with a naive one would raise.
    return parsed.replace(tzinfo=None)


def _in_range(value: str, time_range: TimeRange) -> bool:
    """Whether `value` falls inside `time_range`, or True when either cannot be read as a date."""
    at = _iso(value)
    if at is None:
        return True
    for bound, after in ((time_range.start, True), (time_range.end, False)):
        if bound is None:
            continue
        edge = _iso(bound.period)
        if edge is None:
            return True
        if after and (at < edge or (at == edge and not bound.inclusive)):
            return False
        if not after and (at > edge or (at == edge and not bound.inclusive)):
            return False
    return True


@frozen(unsafe_hash=False)
class ReferencePeriod:
    """Time period bounds for a constraint's data coverage."""

    start_period: datetime = field(converter=as_utc)
    end_period: datetime = field(converter=as_utc)


@frozen(unsafe_hash=False)
class ReleaseCalendar:
    """Release schedule information for a constraint.

    All fields are ISO 8601 duration strings (e.g. ``"P1M"``, ``"P1M15D"``).
    """

    periodicity: str
    offset: str
    tolerance: str


def _to_urls(values: Iterable[str]) -> tuple[str, ...]:
    """URLs as a tuple, refusing one bare string — its characters are not URLs (#261)."""
    if isinstance(values, str):
        msg = f"expected a tuple of URLs, got the string {values!r}; write ({values!r},)"
        raise TypeError(msg)
    return tuple(values)


@frozen
class QueryableDataSource:
    """A data source that answers queries — SDMX ``QueryableDataSourceType``.

    Attributes:
        data_url: Where the data is queried (``DataURL``).
        is_rest_datasource: Whether it is a REST service (``isRESTDatasource``).
        is_web_service_datasource: Whether it is a SOAP service
            (``isWebServiceDatasource``).
        wsdl_url: Its WSDL description (``WSDLURL``).
        wadl_url: Its WADL description (``WADLURL``).
    """

    data_url: str
    is_rest_datasource: bool
    is_web_service_datasource: bool
    wsdl_url: str | None = None
    wadl_url: str | None = None


@frozen
class DataSetReference:
    """A data set, named by its provider and id — SDMX-ML 2.1's ``SetReferenceType``.

    2.1 only: 3.x attaches a constraint to no data set.
    """

    data_provider: Ref[object] = field(converter=to_ref, validator=refuse_urn_text)
    id: str


@frozen(unsafe_hash=False)
class ConstraintAttachment:
    """What a constraint is attached to.

    SDMX makes this a choice, in every format: one data provider, or data
    sets, or simple data sources, or one or more artefacts of a *single* kind
    — data structures, dataflows or provision agreements — which may carry
    queryable data sources beside them. The fields hold each alternative;
    `validate` refuses more than one set at once.

    Attributes:
        data_provider: The provider whose data is constrained.
        data_sets: SDMX-ML 2.1's data sets; 3.x has none.
        simple_data_sources: URLs of SDMX-ML data messages. SDMX-ML 3.1 has
            none.
        data_structures: Data structures the constraint applies to.
        dataflows: Dataflows the constraint applies to.
        provision_agreements: Provision agreements the constraint applies to.
        queryable_data_sources: Services answering for the structures, flows
            or agreements. SDMX-ML 3.1 has none.
    """

    data_provider: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    data_sets: tuple[DataSetReference, ...] = field(factory=tuple, converter=tuple)
    simple_data_sources: tuple[str, ...] = field(factory=tuple, converter=_to_urls)
    data_structures: tuple[Ref[object], ...] = field(factory=tuple, converter=to_refs, validator=refuse_urn_text)
    dataflows: tuple[Ref[object], ...] = field(factory=tuple, converter=to_refs, validator=refuse_urn_text)
    provision_agreements: tuple[Ref[object], ...] = field(factory=tuple, converter=to_refs, validator=refuse_urn_text)
    queryable_data_sources: tuple[QueryableDataSource, ...] = field(factory=tuple, converter=tuple)

    @property
    def is_empty(self) -> bool:
        """True when the attachment names nothing.

        The default `DataConstraint.attachment` is an empty one, so this is a
        normal state for a partially-assembled constraint. It matters on the
        wire because ``<ConstraintAttachment>`` is ``minOccurs="0"`` in every
        SDMX-ML version *and* requires one of its target elements when
        present — so an empty attachment must be omitted rather than emitted
        as a bare wrapper, which is a document the schema rejects.
        """
        return not self.alternatives

    @property
    def alternatives(self) -> tuple[str, ...]:
        """The names of the alternatives this attachment sets — one when well-formed.

        Queryable data sources are not an alternative of their own: they sit
        beside the structures, flows or agreements they answer for.
        """
        return tuple(
            name
            for name, value in (
                ("data_provider", self.data_provider),
                ("data_sets", self.data_sets),
                ("simple_data_sources", self.simple_data_sources),
                ("data_structures", self.data_structures),
                ("dataflows", self.dataflows),
                ("provision_agreements", self.provision_agreements),
            )
            if value
        )


@frozen(unsafe_hash=False)
class DataConstraint(MaintainableArtefact):
    """A constraint that limits the valid key combinations for a dataflow."""

    sdmx_package: ClassVar[str] = "registry"
    sdmx_class: ClassVar[str] = "DataConstraint"
    msg_field: ClassVar[str] = "constraints"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    constraint_type: ConstraintType = field(default=ConstraintType.ALLOWED, converter=ConstraintType.parse)
    attachment: ConstraintAttachment = field(factory=ConstraintAttachment)
    regions: tuple[CubeRegion, ...] = field(factory=tuple, converter=tuple)
    #: Explicit observation keys, as ``<str:DataKeySet>``. `DataConstraintType`
    #: is ``DataKeySet | CubeRegion`` in 3.0/3.1 and 2.1's ContentConstraint
    #: carries both, so a constraint may select by whole key as well as by
    #: per-dimension region. Modelling only the region meant a document
    #: carrying a key set lost it silently (#241) — and made the advice in the
    #: 3.0 refusal of `AttachmentConstraint` ("use a DataConstraint")
    #: impossible to follow. Each set says whether its keys are included or
    #: excluded; flattened into one tuple, an excluded set read as allowed (#479).
    data_key_sets: tuple[DataKeySet, ...] = field(factory=tuple, converter=tuple)
    reference_period: ReferencePeriod | None = None
    release_calendar: ReleaseCalendar | None = None
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

    def allows(self, key: dict[str, str]) -> bool:
        """Return True if *key* satisfies all CubeRegions in this constraint."""
        return all(region.allows(key) for region in self.regions)

    def availability_misfits(self) -> list[str]:
        """Why this constraint has no SDMX 3.1 `AvailabilityConstraint` form; empty when it has one.

        3.1's availability is one included region of one structure, flow or
        agreement, and nothing else: no role (it is Actual by being what it is),
        no key set, no other attachment.

        Returns:
            One phrase per reason, for a refusal to name.
        """
        misfits: list[str] = []
        if self.constraint_type is not ConstraintType.ACTUAL:
            misfits.append(f"its role is {self.constraint_type.value!r}, not 'Actual'")
        targets = (
            *self.attachment.data_structures,
            *self.attachment.dataflows,
            *self.attachment.provision_agreements,
        )
        if len(targets) != 1 or set(self.attachment.alternatives) - _AVAILABILITY_TARGETS:
            misfits.append("it is not attached to exactly one data structure, dataflow or provision agreement")
        if self.attachment.queryable_data_sources:
            misfits.append("it names queryable data sources")
        if len(self.regions) != 1 or not self.regions[0].include:
            misfits.append(f"it has {len(self.regions)} cube regions, not one included region")
        if self.data_key_sets:
            misfits.append(f"it has {len(self.data_key_sets)} data key sets")
        return misfits

    def to_availability_constraint(self) -> AvailabilityConstraint:
        """This Actual constraint as SDMX 3.1 states availability.

        3.1 removed the role from `DataConstraint` and gave Actual content an
        artefact of its own, which has no identity: no id, agency, version,
        name, description, validity, uri or links. Those are lost, and what
        reads back is an `AvailabilityConstraint`. The attachment, region and
        annotations are kept.

        Raises:
            ValueError: If the constraint does not fit; `availability_misfits`
                names why.
        """
        if misfits := self.availability_misfits():
            msg = f"DataConstraint {self.id} has no AvailabilityConstraint form: {'; '.join(misfits)}"
            raise ValueError(msg)
        return AvailabilityConstraint(attachment=self.attachment, region=self.regions[0], annotations=self.annotations)

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


@frozen(unsafe_hash=False)
class AttachmentConstraint(MaintainableArtefact):
    """An SDMX-ML 2.1 attachment constraint.

    A 2.1-only artefact that pairs a set of data keys with the artefacts to
    which they are attached. Sibling of ``DataConstraint`` inside
    ``<str:Constraints>``.

    SDMX 3.0 did not delete the concept — it *unified* it, folding both of
    2.1's constraint artefacts into one `DataConstraint` with a ``role``. So
    the 3.x and SDMX-JSON writers convert rather than refuse; see
    `to_data_constraint`.
    """

    sdmx_package: ClassVar[str] = "registry"
    sdmx_class: ClassVar[str] = "AttachmentConstraint"
    msg_field: ClassVar[str] = "attachment_constraints"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    attachment: ConstraintAttachment = field(factory=ConstraintAttachment)
    data_key_sets: tuple[DataKeySet, ...] = field(factory=tuple, converter=tuple)
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

    def to_data_constraint(self) -> DataConstraint:
        """The SDMX 3.0 form of this constraint.

        Every field of an AttachmentConstraint is also a field of a
        `DataConstraint` — attachment, key set, identity, validity — so the
        conversion loses nothing. The 3.x and SDMX-JSON writers apply it
        instead of refusing, which is what the old refusal advised ("use a
        DataConstraint") and could not be done until `DataConstraint` gained
        key sets.

        **The return trip is not exact.** A 3.0 DataConstraint carrying only a
        key set could have been either 2.1 artefact, and the document does not
        record which, so writing it back as 2.1 yields a ContentConstraint.
        The *content* survives; the artefact kind does not.

        That asymmetry is asserted by `_CONVERTED_ON_WRITE` in the round-trip
        properties, and belongs to the cross-version harness rather
        than to `KNOWN_GAPS` — the latter records a *field* lost on one target,
        where this is an artefact *kind* that changes across a version hop.

        Returns:
            An equivalent `DataConstraint`.
        """
        return DataConstraint(
            id=self.id,
            maintainer=self.maintainer,
            version=self.version,
            name=self.name,
            description=self.description,
            legacy_is_final=self.legacy_is_final,
            attachment=self.attachment,
            data_key_sets=self.data_key_sets,
            annotations=self.annotations,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
        )

    #: The attachment alternatives SDMX 3.1's availability may name.

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


_AVAILABILITY_TARGETS = frozenset({"data_structures", "dataflows", "provision_agreements"})


@frozen(unsafe_hash=False)
class AvailabilityConstraint:
    """SDMX 3.1's statement of what data exists for one structure, flow or agreement.

    3.1 split Actual content out of `DataConstraint` into this. It is not a
    maintainable artefact: `AvailabilityConstraintType` extends
    `AnnotableType`, with no id, agency, version or name. It is the answer to
    an availability query — one included cube region, and optionally how many
    series and observations that query would return — rather than an artefact
    a registry governs. So it is held in `StructureMessage.availability_constraints`
    and in no artefact list, has no URN, and is neither stored nor written to
    any target but SDMX-ML 3.1; the others leave it out (`formats.gaps.dropped`).

    A 3.0 or 2.1 `DataConstraint` with the Actual role is written to 3.1 as one
    when it fits (`DataConstraint.to_availability_constraint`).

    Attributes:
        attachment: The one data structure, dataflow or provision agreement.
        region: The included cube region with data.
        series_count: How many series the availability query would return.
        obs_count: How many observations it would return.
        annotations: The constraint's annotations.
    """

    attachment: ConstraintAttachment
    region: CubeRegion
    series_count: int | None = None
    obs_count: int | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)

    @property
    def target(self) -> Ref[object] | None:
        """What it states availability for, when the attachment names exactly one thing."""
        refs = (
            *self.attachment.data_structures,
            *self.attachment.dataflows,
            *self.attachment.provision_agreements,
        )
        return refs[0] if len(refs) == 1 else None
