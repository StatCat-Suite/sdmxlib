"""DataStructure and its components — Dimension, TimeDimension, Attribute, Measure, Group."""

from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from attrs import evolve, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._items import any_item_ref
from sdmxlib.model._rules import attachment_stated, id_type, maintained_by, named, some_measures, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Code, Codelist, CodelistLike, EnumerationLike
from sdmxlib.model.collections import ItemList, to_itemlist, to_itemlist_opt
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.istring import InternationalString, normalize_tag
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text, to_optional_ref
from sdmxlib.model.representation import (
    DataType,
    Facet,
    Representation,
    to_text_representation,
    to_time_format,
)
from sdmxlib.model.rest_api import ApiVersion
from sdmxlib.model.urn import ParsesUrn, SdmxUrn
from sdmxlib.model.valuelist import ValueList

if TYPE_CHECKING:
    # `metadatastructure` imports this module at runtime: a cycle.
    from sdmxlib.model.metadatastructure import MetadataStructure


class AttachmentLevel(Enum):
    """Where an attribute attaches within the observation structure."""

    DATASET = "dataset"
    OBSERVATION = "observation"
    SERIES = "series"
    GROUP = "group"


class AttributeUsage(Enum):
    """Whether an attribute must be reported.

    SDMX 3.0 / 3.1 ``UsageType`` (per ``SDMXStructureDataStructure.xsd``).
    SDMX 2.1 used a larger enum (``AssignmentStatusType``: Mandatory /
    Conditional); the reader collapses Conditional → OPTIONAL when
    parsing 2.1 inputs.
    """

    MANDATORY = "mandatory"
    OPTIONAL = "optional"

    @classmethod
    def parse(cls, value: str | AttributeUsage | None) -> AttributeUsage | None:
        """Parse any SDMX-flavoured usage string into the canonical enum.

        Accepts the 3.0/3.1 lowercase form (``mandatory``/``optional``)
        and the 2.1 capitalised form (``Mandatory``/``Conditional``);
        ``Conditional`` maps to ``OPTIONAL``. Returns ``None`` for
        ``None`` input. An already-typed ``AttributeUsage`` passes
        through unchanged (so callers can construct an ``Attribute``
        either way without a manual coercion).
        """
        if value is None:
            return None
        if isinstance(value, AttributeUsage):
            return value
        lowered = value.lower()
        if lowered == "mandatory":
            return cls.MANDATORY
        # 2.1 "Conditional" and 3.0 "optional" are the same concept.
        if lowered in ("optional", "conditional"):
            return cls.OPTIONAL
        msg = f"Unrecognised attribute usage {value!r}; expected mandatory or optional."
        # Readers pass the `usage` / `assignmentStatus` attribute straight in,
        # so an unrecognised one means the document is unreadable (#247). Still
        # a `ValueError` for anyone calling `parse` with their own string.
        raise SdmxParseError(msg)


def _to_usage(value: str | AttributeUsage | None) -> AttributeUsage:
    """Attrs converter: a usage, optional when unstated — SDMX 3.x's ``@usage`` default (#421)."""
    return AttributeUsage.parse(value) or AttributeUsage.OPTIONAL


class ConceptIdentityMixin(ParsesUrn):
    """Typed access to the concept_identity Ref on a DSD component."""

    __slots__ = ()

    # Read-only properties each concrete subclass overrides with its attrs field,
    # as `MaintainableArtefact` states its own (see there for why not bare
    # annotations). The body never runs.
    @property
    def concept(self) -> Ref[Concept] | None:
        """The component's concept identity, as a reference."""
        raise NotImplementedError

    @property
    def concept_identity(self) -> Concept:
        """The resolved Concept for this component.

        Raises TypeError if no concept reference is set.
        Raises UnresolvedRef if the concept has not been fetched.
        """
        concept = self.concept
        if not isinstance(concept, Ref):
            msg = f"No concept reference set: {concept!r}"
            raise TypeError(msg)
        return concept()


class RepresentationMixin:
    """Typed access to the codelist Ref on a DSD component."""

    # No cache of the resolved enumeration (#119's): a `Ref` finds its target
    # in its registry on each access (#492), so a cache pinned the first
    # codelist it saw — stale once a fuller fetch replaced it.
    __slots__ = ()

    # Properties each concrete subclass overrides with its attrs field, as
    # `MaintainableArtefact` states its own (see there for why not bare
    # annotations). The bodies never run. No setter: the class is frozen, and
    # `__attrs_post_init__` fills `representation` from the concept as it is
    # built (#492).
    @property
    def concept(self) -> Ref[Concept] | None:
        """The component's concept identity, as a reference."""
        raise NotImplementedError

    @property
    def representation(self) -> Representation:
        """The component's own representation, or None to take the concept's."""
        raise NotImplementedError

    @property
    def enumeration_format(self) -> Facet | None:
        """A format the enumerated codes must also meet (#237)."""
        raise NotImplementedError

    def __attrs_post_init__(self) -> None:
        if self.representation is None and isinstance(self.concept, Ref) and self.concept.resolved:
            core = self.concept().core_representation
            if core is not None:
                # `representation` lives in the concrete subclass slots
                # (attrs-managed), not in this mixin's __slots__. Construction,
                # not mutation: the component takes its concept's default (#492).
                object.__setattr__(self, "representation", core)

    @property
    def is_enumerated(self) -> bool:
        """True if this component is constrained to a codelist."""
        return isinstance(self.representation, Ref)

    @property
    def codes(self) -> ItemList[Code] | None:
        """Codes from the resolved codelist, or None if unresolved.

        **Materialised, which it was not before.** When the DSD came from a
        `LocalRegistry` this returned the `Query` a `LazyCodelist` hands back,
        so the declaration was false and `len(dim.codes)` type-checked and then
        raised (#398). Asking for `Code` objects is asking for them to be
        built, so building them is what makes the promise true — and on a
        579,951-code codelist that is 2.9 s, which is the honest price of the
        objects rather than a hidden cost.

        Use `code_ids()` when ids are enough: 92 ms for the same codelist, and
        the cheap path whichever kind of codelist this resolves to. None for a
        component enumerated by a `ValueList`, which has values, not codes.
        """
        cl = self.resolved_codelist
        return cl.materialize().codes if cl is not None else None

    def code_ids(self) -> list[str] | None:
        """Every code id — or value, for a `ValueList` — or None if unresolved.

        Ids rather than `Code` objects, because that is the one thing every
        kind of resolved enumeration answers cheaply — see `EnumerationLike`.

        Replaces a `codes` property that declared `ItemList[Code]` and returned
        a `Query` whenever the DSD came from a `LocalRegistry`, so
        `len(dim.codes)` type-checked and raised (#398). **A method, unlike
        what it replaces**, and for the reason that bug existed: on a lazy
        codelist this runs SQL, and attribute access that can reach the
        database is exactly what made `dim.codes` read as free when it was not.

        Reach for `resolved_codelist` when you need the codes themselves; what
        it hands back tells you which class you are holding.
        """
        enumeration = self.resolved_enumeration
        return enumeration.code_ids() if enumeration is not None else None

    @property
    def resolved_enumeration(self) -> EnumerationLike | None:
        """What enumerates this component — a codelist or a `ValueList` — or None.

        None for a free-text component and for one whose reference is not
        resolved. The kind-agnostic counterpart to `resolved_codelist`: its ids,
        labels and size answer whichever SDMX 3.x enumerates by (#422).
        """
        representation = self.representation
        if isinstance(representation, Ref) and representation.resolved:
            return representation()
        return None

    @property
    def resolved_codelist(self) -> CodelistLike | None:
        """The resolved codelist, or None for free-text/unresolved components.

        Narrowing helper: returns a non-None codelist exactly when
        ``is_enumerated`` is True, the Ref has been resolved, and it names a
        codelist rather than a `ValueList` (see `resolved_enumeration`). Lets
        callers write ``cl = comp.resolved_codelist; if cl: ...`` instead of
        repeating the ``isinstance(rep, Ref) and rep.resolved`` guard.

        **`CodelistLike`, not `Codelist`.** A DSD read from a `LocalRegistry`
        resolves this ref to a `LazyCodelist`, so the old declaration was
        wrong for every local caller and five public entry points raised
        `TypeError` on it (#398). The protocol is what the two share; use
        ``.codes.ids()``, ``.code_map(lang=)`` and ``len(cl)``, each of which
        takes the cheap path on whichever class this is.
        """
        enumeration = self.resolved_enumeration
        return enumeration if isinstance(enumeration, CodelistLike) else None

    @property
    def codelist(self) -> Codelist:
        """The resolved Codelist for this component.

        Raises TypeError if the representation is not a codelist reference (a
        Facet, or a `ValueList` — read that through `value_list`).
        Raises UnresolvedRef if the codelist has not been fetched.
        """
        enumeration = self._enumeration("codelist")
        if isinstance(enumeration, ValueList):
            msg = "this component is enumerated by a ValueList; read it with `value_list`, not `codelist`"
            raise TypeError(msg)
        return enumeration

    @property
    def value_list(self) -> ValueList:
        """The resolved `ValueList` enumerating this component (SDMX 3.x).

        Raises TypeError if the representation is not a value-list reference
        (a Facet, or a codelist — read that through `codelist`).
        Raises UnresolvedRef if the value list has not been fetched.
        """
        enumeration = self._enumeration("value list")
        if not isinstance(enumeration, ValueList):
            msg = "this component is enumerated by a codelist; read it with `codelist`, not `value_list`"
            raise TypeError(msg)
        return enumeration

    def _enumeration(self, kind: str) -> Codelist | ValueList:
        """The resolved enumeration; TypeError for a Facet, UnresolvedRef if unfetched."""
        representation = self.representation
        if not isinstance(representation, Ref):
            msg = f"Representation is not a {kind} reference: {type(representation).__name__}"
            raise TypeError(msg)
        return representation()


def _to_concept_ref(v: Ref[Concept] | SdmxUrn[Concept] | None) -> Ref[Concept] | None:
    if isinstance(v, SdmxUrn):
        return Ref[Concept].unresolved(v)
    return v


def _to_concept_role(v: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if v is None:
        return ()
    return tuple(v)


def _to_ids_opt(v: tuple[str, ...] | list[str] | None) -> tuple[str, ...] | None:
    return None if v is None else tuple(v)


def _to_ids(v: Iterable[str]) -> tuple[str, ...]:
    """Ids as a tuple, refusing one bare string — its characters are not ids (#261)."""
    if isinstance(v, str):
        msg = f"expected a tuple of ids, got the string {v!r}; write ({v!r},)"
        raise TypeError(msg)
    return tuple(v)


def _to_concept_scheme_ref(
    v: Ref[ConceptScheme] | SdmxUrn[ConceptScheme] | ConceptScheme | Facet | None,
) -> Ref[ConceptScheme] | Facet | None:
    """Coercer for `MeasureDimension.representation`.

    Mirrors `to_representation`, but for the one component whose enumeration is
    a ConceptScheme rather than a Codelist — see `MeasureDimension`.
    """
    if isinstance(v, SdmxUrn):
        return Ref[ConceptScheme].unresolved(v)
    if isinstance(v, ConceptScheme):
        return Ref.to(v)
    if isinstance(v, Ref) and v.urn.artefact_class != "ConceptScheme":
        # Reject at construction rather than failing later on `.concepts`: a
        # MeasureDimension enumerated by anything but a ConceptScheme is not a
        # state this model should be able to hold.
        msg = (
            f"MeasureDimension.representation must reference a ConceptScheme, "
            f"got a {v.urn.artefact_class} ({v.urn}). SDMX 2.1 enumerates the "
            f"measure dimension over concepts, not codes."
        )
        raise TypeError(msg)
    return v


@frozen(unsafe_hash=False)
class Dimension(ConceptIdentityMixin, RepresentationMixin):
    """An identifying component of a DataStructure."""

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "Dimension"

    id: str = field(validator=id_type)
    concept: Ref[Concept] | None = field(default=None, converter=_to_concept_ref, validator=refuse_urn_text)
    representation: Representation = field(default=None, converter=to_text_representation, validator=refuse_urn_text)
    #: Facets constraining the *codes* of an enumerated representation —
    #: SDMX-ML's ``<str:EnumerationFormat>``, SDMX-JSON's
    #: ``enumerationFormat``. Distinct from `representation`, which is either
    #: the codelist reference *or* a free-text facet: a component may carry a
    #: codelist and a format together, saying "the value is a code from
    #: CL_FREQ, and that code is a single-character string".
    #:
    #: Kept as a separate field rather than folded into `representation` so
    #: the documented ``match representation: case Ref() | case Facet()``
    #: idiom keeps working. Every SDMX-ML version and SDMX-JSON allow the
    #: pair; sdmxlib dropped the format silently until #237.
    enumeration_format: Facet | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    concept_role: tuple[str, ...] = field(factory=tuple, converter=_to_concept_role)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)

    @property
    def text_type(self) -> DataType | str | None:
        """Text type if the representation is a Facet, else None.

        Declared as `Facet.type` is, which is what it returns: a `DataType` for
        the types SDMX names, a plain `str` for one it does not. It said
        `str | None`, which is true — `DataType` is a `StrEnum` — but hid the
        enum from a checker narrowing `case DataType.Double:`.
        """
        return self.representation.type if isinstance(self.representation, Facet) else None

    @property
    def enumeration_urn(self) -> SdmxUrn[Codelist | ValueList] | None:
        """URN of the referenced codelist or value list, or None."""
        return self.representation.urn if isinstance(self.representation, Ref) else None


@frozen(unsafe_hash=False)
class TimeDimension(ConceptIdentityMixin):
    """The time dimension of a DataStructure (always id=TIME_PERIOD)."""

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: Its own class, not ``Dimension``: SDMX gives the time dimension a
    #: distinct URN class in both 2.1 and 3.0.
    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "TimeDimension"

    id: str = "TIME_PERIOD"
    concept: Ref[Concept] | None = field(default=None, converter=_to_concept_ref, validator=refuse_urn_text)
    #: Always a format: one stated, or `ObservationalTimePeriod`, the default
    #: every SDMX format gives a time dimension that states none (#507).
    representation: Facet = field(default=None, converter=to_time_format)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)

    @property
    def text_type(self) -> DataType | str | None:
        """Text type from the representation, as `Facet.type` declares it."""
        return self.representation.type


#: The id SDMX-ML 2.1 fixes for its ``ReportingYearStartDay`` attribute and
#: reserves for it: an attribute by this id *is* that one. SDMX 3.x has no
#: element for it, and holds it as an ordinary attribute by the same id (#422).
REPORTING_YEAR_START_DAY = "REPORTING_YEAR_START_DAY"


@frozen(unsafe_hash=False)
class Attribute(ConceptIdentityMixin, RepresentationMixin):
    """A descriptive component attached to an observation, series, or dataset."""

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: The 2.1 spelling, which is what the rest of the library canonicalises on
    #: (`storage._kinds.COMPONENT_TYPES`, `mapping._COMPONENT_LOCATIONS`) and
    #: what a 2.1 ``<Ref>`` omitting ``@class`` means. 3.0 spells it
    #: ``DataAttribute``, folded onto this one by
    #: `validation._CLASS_ALIAS_GROUPS`.
    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "Attribute"

    id: str = field(validator=id_type)
    #: Whether a value must be reported. SDMX 3.x's ``@usage`` defaults to
    #: optional, so an unstated usage *is* optional and the field is never
    #: None (#421); 2.1's ``assignmentStatus`` is required, Conditional read
    #: as optional.
    usage: AttributeUsage = field(default=AttributeUsage.OPTIONAL, converter=_to_usage)
    #: What the attribute's values attach to. Required, with no default: every
    #: SDMX format requires the relationship, so an attribute without one has
    #: no form anywhere, and no level is safe to assume for it (#421, #508).
    #: Keyword-only, since the fields before it have defaults.
    attachment: AttachmentLevel = field(kw_only=True, validator=attachment_stated)
    related_dimensions: list[str] | None = None
    #: 3.0's `<str:Dimension optional="true">` inside AttributeRelationship:
    #: the related dimensions a value may be reported without (#422). Always
    #: a subset of `related_dimensions`; 2.1 has no such flag.
    optional_dimensions: frozenset[str] = field(factory=frozenset, converter=frozenset)
    # There is deliberately no ``related_measure`` *in the attachment* (#360):
    # 3.0's ``AttributeRelationshipType`` has no measure branch, and 2.1's
    # ``<PrimaryMeasure><Ref id=…/>`` only ever names ``OBS_VALUE``.
    #
    #: Which measures the attribute applies to — 3.0's `MeasureRelationship`,
    #: SDMX-JSON's ``measureRelationship`` (#422). A sibling of the
    #: attachment, not part of it: an attribute attached to series can still
    #: concern only some measures. None means all of them, as the IM and
    #: SDMX-JSON's schema say of its absence; empty means nothing, and is
    #: refused as it is set (#497). 2.1 has no form for it.
    related_measures: tuple[str, ...] | None = field(default=None, converter=_to_ids_opt, validator=some_measures)
    #: When ``attachment is AttachmentLevel.GROUP``, the id of the
    #: ``<str:Group>`` the attribute is bound to. The XSD models this
    #: as a reference to a specific GroupDimension descriptor — emitted
    #: as ``<Group><Ref id=...></Group>`` in 2.1 and ``<Group>id</Group>``
    #: text in 3.0+. None when the attachment isn't group-level.
    related_group: str | None = None
    #: SDMX-ML 2.1's ``AttachmentGroup``: groups an attribute related to
    #: dimensions also attaches to, for the attribute's value to be reported
    #: with a group's key (#422). Beside `related_dimensions`, never instead
    #: of them; SDMX 3.x has no form for it.
    attachment_groups: tuple[str, ...] = field(factory=tuple, converter=_to_ids)
    concept: Ref[Concept] | None = field(default=None, converter=_to_concept_ref, validator=refuse_urn_text)
    representation: Representation = field(default=None, converter=to_text_representation, validator=refuse_urn_text)
    #: Facets constraining the *codes* of an enumerated representation —
    #: SDMX-ML's ``<str:EnumerationFormat>``, SDMX-JSON's
    #: ``enumerationFormat``. Distinct from `representation`, which is either
    #: the codelist reference *or* a free-text facet: a component may carry a
    #: codelist and a format together, saying "the value is a code from
    #: CL_FREQ, and that code is a single-character string".
    #:
    #: Kept as a separate field rather than folded into `representation` so
    #: the documented ``match representation: case Ref() | case Facet()``
    #: idiom keeps working. Every SDMX-ML version and SDMX-JSON allow the
    #: pair; sdmxlib dropped the format silently until #237.
    enumeration_format: Facet | None = None
    #: How many values one observation may carry — 3.0's multi-valued
    #: components, `LocalRepresentation/@minOccurs` and `@maxOccurs` (#422).
    #: None is unbounded, as on `MetadataAttribute`. 2.1 has no form for it.
    min_occurs: int = 1
    max_occurs: int | None = 1
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    concept_role: tuple[str, ...] = field(factory=tuple, converter=_to_concept_role)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


@frozen(unsafe_hash=False)
class Measure(ConceptIdentityMixin, RepresentationMixin):
    """An observed value component (PrimaryMeasure in v2.1, Measure in v3.0).

    In SDMX 2.1, a DSD has exactly one measure (``OBS_VALUE``). In SDMX 3.0+,
    a DSD may declare multiple measures — each observation then carries all
    measure values as separate columns. This covers both multi-valued
    observations (e.g. a value with confidence bounds) and wide microdata
    (e.g. census records with many variables per person).
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: 2.1 spells the same component ``PrimaryMeasure``, which
    #: `validation._CLASS_ALIAS_GROUPS` folds onto this one.
    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "Measure"

    id: str = field(validator=id_type)
    concept: Ref[Concept] | None = field(default=None, converter=_to_concept_ref, validator=refuse_urn_text)
    representation: Representation = field(default=None, converter=to_text_representation, validator=refuse_urn_text)
    #: See `Dimension.enumeration_format` — the same pairing applies here.
    enumeration_format: Facet | None = None
    #: See `Attribute.min_occurs` (#422).
    min_occurs: int = 1
    max_occurs: int | None = 1
    #: SDMX 3.x ``@usage``: whether a value must be reported for every
    #: observation, as an attribute's (#422). 2.1's primary measure has none.
    #: Optional unless stated, as the schema defaults it (#421).
    usage: AttributeUsage = field(default=AttributeUsage.OPTIONAL, converter=_to_usage)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    concept_role: tuple[str, ...] = field(factory=tuple, converter=_to_concept_role)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


@frozen(unsafe_hash=False)
class MeasureDimension(ConceptIdentityMixin):
    """The measure dimension in an SDMX 2.1 cross-sectional DSD.

    Acts as a regular dimension whose values name the measures present in the
    dataset (e.g. EMPLOYMENT, UNEMPLOYMENT for an LFS flow). Its presence is
    the formal SDMX 2.1 signal that data arrives in long format with one
    ``OBS_VALUE`` per row — a pivot on this dimension produces the wide
    (multi-measure) view analysts expect.

    Unlike every other component, this one is enumerated by a **ConceptScheme**,
    not a Codelist: the values naming the measures are concepts. The XSD types
    the enumeration ``ConceptSchemeReferenceType``. That is why this class does
    *not* use `RepresentationMixin` — the mixin's ``representation`` is typed
    for a Codelist, and narrowing a mutable attribute in a subclass is not
    sound. It carries its own field and its own accessors, named for what they
    actually hold: `concept_scheme` and `concepts`. There is deliberately no
    ``codelist``/``codes`` alias — those names were wrong here.

    SDMX 3.0 removes MeasureDimension entirely. Multi-measure data is instead
    modelled with multiple `Measure` components in the MeasureDescriptor,
    giving each observation all measure values as columns (wide format). The
    two patterns produce different data shapes: MeasureDimension is long
    (one value per row, pivot to get wide), while 3.0 measures are natively
    columnar.
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: 2.1 only; 3.0 removed the component and the URN class with it.
    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "MeasureDimension"

    id: str
    concept: Ref[Concept] | None = field(default=None, converter=_to_concept_ref, validator=refuse_urn_text)
    representation: Ref[ConceptScheme] | Facet | None = field(
        default=None, converter=_to_concept_scheme_ref, validator=refuse_urn_text
    )
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: As the other components' (#422).
    concept_role: tuple[str, ...] = field(factory=tuple, converter=_to_concept_role)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None

    @property
    def is_enumerated(self) -> bool:
        """True if this component is constrained to a ConceptScheme."""
        return isinstance(self.representation, Ref)

    @property
    def resolved_concept_scheme(self) -> ConceptScheme | None:
        """The resolved ConceptScheme, or None for free-text/unresolved.

        Narrowing helper mirroring `RepresentationMixin.resolved_codelist`:
        non-None exactly when ``is_enumerated`` and the Ref has been resolved.
        """
        if isinstance(self.representation, Ref) and self.representation.resolved:
            return self.representation()
        return None

    @property
    def concept_scheme(self) -> ConceptScheme:
        """The resolved ConceptScheme enumerating this measure dimension.

        Raises:
            TypeError: The representation is a `Facet`, not a reference.
            UnresolvedRef: The ConceptScheme has not been fetched.
        """
        representation = self.representation
        if not isinstance(representation, Ref):
            msg = f"Representation is not a concept-scheme reference: {type(representation).__name__}"
            raise TypeError(msg)
        return representation()

    @property
    def concepts(self) -> ItemList[Concept] | None:
        """Concepts from the resolved ConceptScheme, or None if unresolved."""
        cs = self.resolved_concept_scheme
        return cs.concepts if cs is not None else None


@frozen(unsafe_hash=False)
class Group(ParsesUrn):
    """A dimension grouping (v2.1). None for v3.0 sources."""

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: SDMX names a group's URN class for the *descriptor* that holds it —
    #: ``datastructure.GroupDimensionDescriptor=AG:DSD(1.0).G`` in both 2.1 and
    #: 3.0 — so this pair is not read off the class name.
    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "GroupDimensionDescriptor"

    id: str
    dimension_ids: list[str] = field(factory=list)
    #: SDMX-ML 2.1's alternative to `dimension_ids`: the attachment constraint
    #: whose keys make up the group (#422). One or the other; SDMX 3.x has
    #: only dimensions.
    attachment_constraint: Ref[object] | None = field(
        default=None, converter=to_optional_ref, validator=refuse_urn_text
    )
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None


class ComponentRole(Enum):
    """What part of a dataflow's shape a component plays.

    The question a backend asks before deciding whether it can filter on a
    component.

    The SDMX REST data endpoint constrains different components by different
    means — the positional key, `startPeriod`/`endPeriod`, `measures=`,
    `c[<id>]=` — so "which component is this" is not the useful question and
    "what can be done with it" is. A capability declares itself over these
    rather than over column names, because an observation's columns come from a
    DSD: `column="REF_AREA"` is a fact about one dataflow, not about a backend.

    `KEY_DIMENSION` and `TIME_DIMENSION` are split because the time dimension is
    the one component reachable *two* ways — as a key slot, which takes equality
    or membership, and as `startPeriod`/`endPeriod`, which takes a **range**. A
    backend that can serve a range on time can serve nothing of the sort on any
    other dimension, and collapsing them would claim otherwise.
    """

    KEY_DIMENSION = "key_dimension"
    TIME_DIMENSION = "time_dimension"
    MEASURE = "measure"
    ATTRIBUTE = "attribute"


@frozen(unsafe_hash=False)
class MetadataAttributeUsage:
    """A metadata attribute a DataStructure uses, and what it attaches to.

    SDMX 3.0 lets a DSD report reference metadata alongside data: it names an
    MSD (`DataStructure.metadata`) and, in its AttributeList, uses some of that
    MSD's attributes, each attached like a data attribute. The usage has no id
    of its own — the schema prohibits one — and is known by the attribute it
    uses: `id` is that attribute's id, local to `DataStructure.metadata`
    (3.0's `MetadataAttributeReference`).

    Attributes:
        id: The id of the MSD attribute used.
        attachment: What it attaches to; as `Attribute.attachment`.
        related_dimensions: As `Attribute.related_dimensions`.
        optional_dimensions: As `Attribute.optional_dimensions`.
        related_group: As `Attribute.related_group`.
        annotations: The usage's annotations.
    """

    id: str
    attachment: AttachmentLevel = field(validator=attachment_stated)
    related_dimensions: list[str] | None = None
    optional_dimensions: frozenset[str] = field(factory=frozenset, converter=frozenset)
    related_group: str | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` (#422). See `MaintainableArtefact.uri`.
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


# A lazily evaluated alias rather than the union written out: attrs evaluates a
# converter's annotations when the class is built (attrs#1470, open), and
# `MetadataStructure` is imported for type checking alone, to break a cycle.
type _MsdRefLike = Ref[MetadataStructure] | SdmxUrn[MetadataStructure] | None
type _MsdRef = Ref[MetadataStructure] | None


def _to_msd_ref(v: _MsdRefLike) -> _MsdRef:
    if isinstance(v, SdmxUrn):
        return Ref.unresolved(v)
    return v


@frozen(unsafe_hash=False)
class DataStructure(MaintainableArtefact):
    """Defines the shape of a dataset — dimensions, attributes, measures.

    Example:
        dsd.dimensions["FREQ"].codes  # ItemList[Code] if resolved
        dsd.code_map(lang="en")  # {dim_id: {code_id: label}}
        dsd.all_codelists  # {dim_id: Codelist}
        dsd.column_labels(lang="en")  # {dim_id: concept label}
        dsd.enumerated_dimensions  # ItemList[Dimension]
    """

    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "DataStructure"
    msg_field: ClassVar[str] = "data_structures"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    dimensions: ItemList[Dimension] = field(factory=ItemList, converter=to_itemlist)
    time_dimension: TimeDimension | None = None
    measure_dimension: MeasureDimension | None = None
    attributes: ItemList[Attribute] = field(factory=ItemList, converter=to_itemlist)
    measures: ItemList[Measure] = field(factory=ItemList, converter=to_itemlist)
    groups: ItemList[Group] | None = field(default=None, converter=to_itemlist_opt)
    #: The annotations of the lists around `dimensions`, `attributes` and
    #: `measures` — SDMX's DimensionDescriptor, AttributeDescriptor and
    #: MeasureDescriptor, which this model does not reify (#422).
    dimension_descriptor_annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    attribute_descriptor_annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    measure_descriptor_annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: Each list's ``@uri`` (#422), beside its annotations for the same reason.
    dimension_descriptor_uri: str | None = None
    attribute_descriptor_uri: str | None = None
    measure_descriptor_uri: str | None = None
    #: Each list's ``<com:Link>`` elements (#422), likewise.
    dimension_descriptor_links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    attribute_descriptor_links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    measure_descriptor_links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX 3.1 ``@evolvingStructure``: the structure may gain components in
    #: later versions without a major version change (#422).
    evolving_structure: bool = False
    #: The MSD whose attributes `metadata_attributes` uses — 3.0's
    #: `<str:Metadata>`, SDMX-JSON's ``metadata`` (#422). 2.1 has no form for it.
    metadata: Ref[MetadataStructure] | None = field(default=None, converter=_to_msd_ref, validator=refuse_urn_text)
    #: The metadata attributes reported with this DSD's data — 3.0's
    #: `MetadataAttributeUsage`s, kept apart from `attributes` so that list
    #: stays data attributes only (#422).
    metadata_attributes: ItemList[MetadataAttributeUsage] = field(factory=ItemList, converter=to_itemlist)
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

    def ref_to(self, component_id: str) -> Ref[object]:
        """Build a resolved `Ref` to a component of this structure, by id.

        Dispatches on which list holds the id — `dimensions`, the time or
        measure dimension, `measures`, `attributes` or `groups` — so the caller
        states the id and nothing else. That is unambiguous rather than merely
        tidy: `storage.schema` declares ``PRIMARY KEY (dsd_urn, component_id)``
        on ``dsd_component``, so a component id is already unique across the
        whole structure in this library, whichever list it sits in.
        `validation` enforces the same thing on the model.

        `Ref[object]`, not a six-way union: the class varies, every field that
        consumes one of these is declared `Ref[object]`
        (`HierarchyAssociation.linked_object`, `Categorisation.target`), and the
        URN carries the real class, so nothing is lost at runtime.

        Raises:
            KeyError: If no component has this id, naming the lists searched.

        Example:
            HierarchyAssociation(..., linked_object=dsd.ref_to("REF_AREA"))
        """
        return any_item_ref(self, component_id)

    @property
    def components(self) -> ItemList[Dimension | MeasureDimension | Attribute | Measure]:
        """All components: dimensions + measure_dimension (if any) + attributes + measures."""
        md: list[MeasureDimension] = [self.measure_dimension] if self.measure_dimension is not None else []
        return ItemList([*self.dimensions, *md, *self.attributes, *self.measures])

    def find_component(
        self, component_id: str
    ) -> Dimension | TimeDimension | MeasureDimension | Attribute | Measure | None:
        """The component with this id, whatever kind it is, or None.

        `components` deliberately omits the time dimension — it is not part of
        the ordinary component lists — so it cannot answer "does this structure
        define X". This can, which is what a caller holding only an id needs.

        Args:
            component_id: Id to look for.

        Returns:
            The component, or None if the structure defines no such id.
        """
        for component in (*self.dimensions, *self.attributes, *self.measures):
            if component.id == component_id:
                return component
        if self.time_dimension is not None and self.time_dimension.id == component_id:
            return self.time_dimension
        if self.measure_dimension is not None and self.measure_dimension.id == component_id:
            return self.measure_dimension
        return None

    @property
    def key_dimensions(self) -> list[Dimension | TimeDimension]:
        """Dimension components in DSD order — regular dimensions, then time.

        **Not, on its own, the shape of a REST key**, which is what an earlier
        version of this docstring claimed. Whether the time dimension
        occupies a segment depends on the API version, so ask
        `key_slot_ids` and say which. This property is the raw ordering both
        answers are built from.
        """
        dims: list[Dimension | TimeDimension] = list(self.dimensions)
        if self.time_dimension is not None:
            dims.append(self.time_dimension)
        return dims

    def key_slot_ids(self, api: ApiVersion) -> list[str]:
        """Positional slots of a REST data key, in order, for one API version.

        The two SDMX-REST generations disagree about the time dimension, and
        the disagreement is silent — a key of the wrong length is either
        rejected outright or, worse, read with every value one slot out:

        | API | slots | time filtered by |
        | --- | --- | --- |
        | `"2.1"` (REST 1.5) | dimensions, without time | `startPeriod` / `endPeriod` |
        | `"3.0"` (REST 2.2.2) | dimensions, then time | a segment, or the periods |

        ECB's canonical `M.USD.EUR.SP00.A` is the 2.1 shape: five segments for
        a six-dimension structure. sdmx1 excludes time for the same reason,
        commenting "TimeDimensions handled by query parameters".

        A formal `MeasureDimension` occupies a slot too, and it goes **after
        the regular dimensions and before time** — the order the 2.1
        DimensionList declares. The slot exists whether or not the caller
        selects any measures; unconstrained, it is simply an empty segment.

        Args:
            api: The REST API version, required and never inferred. A default
                would build a wrong-shaped key silently, which is the failure
                this method exists to prevent.

        Returns:
            Component ids, in the order their values appear in the key.
        """
        slots = [d.id for d in self.dimensions]
        if self.measure_dimension is not None:
            slots.append(self.measure_dimension.id)
        if api == "3.0" and self.time_dimension is not None:
            slots.append(self.time_dimension.id)
        return slots

    def role_of(self, component_id: str) -> ComponentRole | None:
        """What part of the shape `component_id` plays, or `None` if unknown.

        The question a backend asks before deciding whether it can filter on a
        column. See `ComponentRole` for why the roles are cut this way.

        `None` means *this structure does not define that component* — not
        "no role". A caller must treat it as unpushable rather than guessing,
        because the alternative is building a positional key with a wrong slot,
        which queries a different slice silently instead of failing.

        Args:
            component_id: The SDMX component id, not a physical column name.

        Returns:
            The role, or `None` when the structure has no such component.
        """
        if self.time_dimension is not None and self.time_dimension.id == component_id:
            return ComponentRole.TIME_DIMENSION
        # `key_dimensions` is dimensions-then-time and is the SDMX key order, so
        # membership in it is exactly "occupies a positional slot".
        if any(d.id == component_id for d in self.dimensions):
            return ComponentRole.KEY_DIMENSION
        if any(m.id == component_id for m in self.measures):
            return ComponentRole.MEASURE
        if any(a.id == component_id for a in self.attributes):
            return ComponentRole.ATTRIBUTE
        if self.measure_dimension is not None and self.measure_dimension.id == component_id:
            # A 2.1 MeasureDimension is a dimension in the key, whatever it
            # names — `_serialise_key` gives it a slot for that reason.
            return ComponentRole.KEY_DIMENSION
        return None

    @property
    def enumerated_dimensions(self) -> ItemList[Dimension]:
        """Dimensions that are constrained to a codelist."""
        return self.dimensions.filter(lambda d: d.is_enumerated)

    @property
    def free_text_dimensions(self) -> ItemList[Dimension]:
        """Dimensions that are not enumerated."""
        return self.dimensions.filter(lambda d: not d.is_enumerated)

    @property
    def all_codelists(self) -> dict[str, Codelist]:
        """All resolved codelists keyed by component id."""
        result: dict[str, Codelist] = {}
        for comp in [*self.dimensions, *self.attributes, *self.measures]:
            if isinstance(comp.representation, Ref) and comp.representation.resolved:
                enumeration = comp.representation()
                if not isinstance(enumeration, ValueList):
                    result[comp.id] = enumeration
        return result

    def code_map(self, *, lang: str) -> dict[str, dict[str, str]]:
        """Build ``{component_id: {code_id: label}}`` for all resolved enumerations.

        A `ValueList`-enumerated component is included, keyed by its values.

        A `LazyCodelist` answers this with one SQL projection, cached and
        single-flight at the source; iterating its codes instead would build a
        `Code` and an `InternationalString` per row to read one string off each
        (CL_GEOGRAPHY: 78k codes, ~16s). `sdmxlib.polars` takes the same path.

        Raises:
            ValueError: If `lang` is not a well-formed language tag.
        """
        lang = normalize_tag(lang)
        return {
            comp.id: enumeration.code_map(lang=lang)
            for comp in [*self.dimensions, *self.attributes, *self.measures]
            if (enumeration := comp.resolved_enumeration) is not None
        }

    def column_labels(self, *, lang: str) -> dict[str, str]:
        """Map component ids to human labels from their concepts.

        Raises:
            ValueError: If `lang` is not a well-formed language tag.
        """
        key = normalize_tag(lang)
        result: dict[str, str] = {}
        for comp in [*self.dimensions, *self.attributes, *self.measures]:
            if isinstance(comp.concept, Ref) and comp.concept.resolved:
                result[comp.id] = comp.concept_identity.name._text(key) or comp.id
            else:
                result[comp.id] = comp.id
        return result

    def add(
        self,
        items: Dimension
        | MeasureDimension
        | Attribute
        | Measure
        | list[Dimension | MeasureDimension | Attribute | Measure],
    ) -> DataStructure:
        """Return a new DataStructure with *items* added to the appropriate list(s).

        Dispatches by type — dimensions go to dimensions, attributes to
        attributes, measures to measures. Accepts a single component or a
        mixed list.

        Example:
            dsd = dsd.add(sl.Dimension(id="PARTNER", representation=cl))
            dsd = dsd.add([sl.Dimension(id="PARTNER", representation=cl),
                           sl.Attribute(id="OBS_STATUS")])
        """
        item_list = [items] if not isinstance(items, list) else items
        dims = list(self.dimensions)
        attrs = list(self.attributes)
        meas = list(self.measures)
        md = self.measure_dimension
        for item in item_list:
            match item:
                case MeasureDimension():
                    md = item
                case Dimension():
                    dims.append(item)
                case Attribute():
                    attrs.append(item)
                case Measure():
                    meas.append(item)
        return evolve(self, dimensions=dims, measure_dimension=md, attributes=attrs, measures=meas)

    def drop(self, component_ids: str | list[str]) -> DataStructure:
        """Return a new DataStructure with the given component(s) removed.

        Searches dimensions, attributes, and measures by id.
        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not found in any component list.

        Example:
            dsd = dsd.drop("SECTOR")
            dsd = dsd.drop(["SECTOR", "OBS_STATUS"])
        """
        ids = [component_ids] if isinstance(component_ids, str) else list(component_ids)
        all_ids = {d.id for d in self.dimensions} | {a.id for a in self.attributes} | {m.id for m in self.measures}
        missing = [i for i in ids if i not in all_ids]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(
            self,
            dimensions=[d for d in self.dimensions if d.id not in id_set],
            attributes=[a for a in self.attributes if a.id not in id_set],
            measures=[m for m in self.measures if m.id not in id_set],
        )

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


#: Any component of a `DataStructure`'s descriptors. Spelled once here rather
#: than inline at each use — the members differ in which optional fields they
#: carry (``usage`` on Attribute, ``concept_role`` on Dimension, …), so callers
#: still reach for ``getattr`` on the non-shared ones; the union's job is to
#: reject something that is not a DSD component at all.
type Component = Dimension | TimeDimension | MeasureDimension | Attribute | Measure
