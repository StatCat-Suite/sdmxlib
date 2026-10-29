"""MetadataStructure (MSD) and MetadataAttribute — structural template for reference metadata."""

from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar, TypeIs, override

from attrs import evolve, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._items import item_ref
from sdmxlib.model._refshape import derived_view
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model._tree import PathIndex, PathNavigation
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.collections import ItemList, to_itemlist
from sdmxlib.model.concept import Concept
from sdmxlib.model.datastructure import ConceptIdentityMixin, RepresentationMixin
from sdmxlib.model.errors import ValidationError, ValidationIssue
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text
from sdmxlib.model.representation import DataType, Facet, Representation, to_text_format
from sdmxlib.model.urn import ParsesUrn, SdmxUrn
from sdmxlib.model.valuelist import ValueList


def _to_concept_ref(v: Ref[Concept] | SdmxUrn[Concept] | None) -> Ref[Concept] | None:
    if isinstance(v, SdmxUrn):
        return Ref[Concept].unresolved(v)
    return v


#: A 2.1 target kind → the ``textType`` its text format fixes, or for a report
#: period defaults (#503).
_TARGET_TEXT_TYPE: dict[str, str] = {
    "ReportPeriodTarget": DataType.ObservationalTimePeriod,
    "KeyDescriptorValuesTarget": "KeyValues",
    "DataSetTarget": "DataSetReference",
    "ConstraintContentTarget": "AttachmentConstraintReference",
    "IdentifiableObjectTarget": "IdentifiableReference",
}


@frozen(unsafe_hash=False)
class TargetObject:
    """One kind of thing a 2.1 `MetadataTarget` lets a report describe.

    SDMX-ML 2.1's ``TargetObject``: one of five elements, and a metadata
    target holds one or more of them (#422) — a report may then describe, say,
    an identifiable object *within* a data set. 2.1 models the kind as a
    choice of element, not an attribute, so `kind` is the element's name.

    Attributes:
        id: The element's own id, which 2.1 fixes for every kind but
            ``IdentifiableObjectTarget``.
        kind: Which of 2.1's five target elements this is — for example
            ``"IdentifiableObjectTarget"``.
        object_type: The artefact class an ``IdentifiableObjectTarget``
            points at; None for the other kinds.
        representation: Its ``<str:LocalRepresentation>``, which 2.1
            requires on every kind.
        annotations: Its annotations, which 2.1 gives it apart from the
            metadata target's.
        uri: Its ``@uri``.
    """

    id: str
    kind: str = "IdentifiableObjectTarget"
    object_type: str | None = None
    representation: Facet | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None

    def __attrs_post_init__(self) -> None:
        """Its format's type, where SDMX-ML 2.1 fixes or defaults it for this `kind` (#503).

        Set here rather than by a converter: which type it is depends on `kind`.
        """
        fixed = _TARGET_TEXT_TYPE.get(self.kind)
        if fixed is not None and self.representation is not None and self.representation.type is None:
            object.__setattr__(self, "representation", evolve(self.representation, type=fixed))


def _to_target_objects(value: Iterable[TargetObject]) -> tuple[TargetObject, ...]:
    """Attrs converter: target objects in any iterable, each a `TargetObject`.

    Raises:
        TypeError: If an element is not a `TargetObject`.
    """
    objects = tuple(value)
    for obj in objects:
        if not isinstance(obj, TargetObject):
            msg = f"target_objects must be an iterable of TargetObject, got a {type(obj).__name__}"
            raise TypeError(msg)
    return objects


@frozen(unsafe_hash=False)
class MetadataTarget(ParsesUrn):
    """What a 2.1 metadata structure declares its reports may describe.

    SDMX-ML 2.1 puts this question in the *structure*: an MSD names one or
    more ``<str:MetadataTarget>``, each a combination of the kinds of thing
    metadata may be reported against. SDMX 3.0 moved it out — a
    `Metadataflow` declares its targets and a `MetadataSet` names the actual
    ones — so this layer has no 3.x counterpart and is written only to 2.1.

    Modelled rather than synthesised, for the same reason as `StructureSet`:
    the alternative is inventing a target on write and discarding it on read,
    which makes a 2.1 round-trip return something the caller did not supply
    (#245).

    2.1 gives the target an id, which report structures reference, and each
    target object inside it another; they usually differ, so both are held.

    Attributes:
        id: The ``<MetadataTarget>`` id, referenced by a `ReportStructure`.
        target_objects: Its target objects, in document order — one or more,
            as 2.1 requires (#422).
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: 2.1 only, as for `ReportStructure`.
    sdmx_package: ClassVar[str] = "metadatastructure"
    sdmx_class: ClassVar[str] = "MetadataTarget"

    id: str
    target_objects: tuple[TargetObject, ...] = field(factory=tuple, converter=_to_target_objects)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None


@frozen(unsafe_hash=False)
class ReportStructure(ParsesUrn):
    """A 2.1 grouping of metadata attributes against a set of targets.

    Holds *ids* rather than the attributes themselves, so the attributes stay
    single-sourced in `MetadataStructure.attributes` — the 3.0-shaped view
    this model is built around. Copying them here would denormalise one fact
    into two places and let the copies disagree, which is the design mistake
    rejected for `StructureSet` in #239.

    Attributes:
        id: The report structure's id.
        attribute_ids: Ids of the MSD attributes that belong to this report,
            in document order.
        target_ids: Ids of the `MetadataTarget`s it reports against.
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: 2.1 only; 3.0 dropped the report-structure layer, and the URN class with
    #: it.
    sdmx_package: ClassVar[str] = "metadatastructure"
    sdmx_class: ClassVar[str] = "ReportStructure"

    id: str
    attribute_ids: tuple[str, ...] = field(factory=tuple, converter=tuple)
    target_ids: tuple[str, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None


def _value_list_refusal(urn: SdmxUrn[object]) -> ValidationError:
    msg = f"an enumerated metadata attribute uses a Codelist (IM §7.3.4.1), and {urn} is a ValueList"
    return ValidationError([ValidationIssue(path="MetadataAttribute.representation", message=msg)])


def _names_codelist(ref: Ref[Codelist | ValueList]) -> TypeIs[Ref[Codelist]]:
    return ref.urn.artefact_class != "ValueList"


def codelist_representation(representation: Representation) -> Ref[Codelist] | Facet | None:
    """A component representation as a metadata attribute's: a codelist or a text format.

    For the readers, which build every component's representation one way.
    "If the Representation is enumerated, then it must use a Codelist" (IM
    §7.3.4.1); the 3.0 and 3.1 XSDs and the SDMX-JSON 2.0 schema all type the
    enumeration a codelist reference.

    Raises:
        ValidationError: If `representation` refers to a `ValueList`.
    """
    if isinstance(representation, Ref) and not _names_codelist(representation):
        raise _value_list_refusal(representation.urn)
    return representation


def to_codelist_representation(
    v: Ref[Codelist] | SdmxUrn[Codelist] | Codelist | Facet | None,
) -> Ref[Codelist] | Facet | None:
    """Attrs converter for a metadata attribute's representation: a codelist or a text format.

    The types refuse a value list; this refuses one an untyped caller passes.

    Raises:
        ValidationError: If `v` is, names or refers to a `ValueList`.
    """
    # A Ref or an artefact carries its URN; a URN is its own.
    urn: object = getattr(v, "urn", v)
    if isinstance(urn, SdmxUrn) and urn.artefact_class == "ValueList":
        raise _value_list_refusal(urn)
    if isinstance(v, SdmxUrn):
        return Ref[Codelist].unresolved(v)
    if isinstance(v, Codelist):
        return Ref.to(v)
    if isinstance(v, Facet):
        return to_text_format(v)
    return v


@frozen(unsafe_hash=False)
class MetadataAttribute(ConceptIdentityMixin, RepresentationMixin):
    """A structural component of a MetadataStructure.

    Defines one slot in the metadata schema — what can be reported, with
    which representation (codelist or text), and how often.

    Children form a recursive tree (no parent back-reference). Cardinality
    is expressed via min_occurs and max_occurs (None = unbounded).

    Example:
        sl.MetadataAttribute(
            id="FOOTNOTE_TEXT",
            concept=cs.ref_to("FOOTNOTE_TEXT"),
            representation=Facet(type=DataType.String),
            min_occurs=0,
            max_occurs=1,
        )
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    sdmx_package: ClassVar[str] = "metadatastructure"
    sdmx_class: ClassVar[str] = "MetadataAttribute"

    id: str = field(validator=id_type)
    concept: Ref[Concept] | None = field(default=None, converter=_to_concept_ref, validator=refuse_urn_text)
    #: A codelist or a text format: a value list is refused (IM §7.3.4.1).
    representation: Ref[Codelist] | Facet | None = field(
        default=None, converter=to_codelist_representation, validator=refuse_urn_text
    )
    #: Facets constraining the *codes* of an enumerated representation, as on a
    #: DSD component — see `Dimension.enumeration_format`.
    #: ``MetadataAttributeRepresentationType`` allows the pairing in every
    #: SDMX-ML version and in SDMX-JSON.
    enumeration_format: Facet | None = None
    min_occurs: int = 0
    max_occurs: int | None = 1
    is_presentational: bool = False
    children: ItemList[MetadataAttribute] = field(factory=ItemList, converter=to_itemlist)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)

    @override
    def __attrs_post_init__(self) -> None:
        """Take the concept's representation when none is given; refuse it if it is a value list."""
        super().__attrs_post_init__()
        representation = self.representation
        if isinstance(representation, Ref) and representation.urn.artefact_class == "ValueList":
            raise _value_list_refusal(representation.urn)


@frozen(unsafe_hash=False)
class MetadataStructure(MaintainableArtefact, PathNavigation[MetadataAttribute]):
    """The structural definition of a reference-metadata schema (SDMX MSD).

    Holds a tree of MetadataAttribute components. Each MetadataAttribute
    declares one reportable slot — a concept, a representation (codelist
    or facet), and cardinality bounds.

    Example:
        msd = sl.MetadataStructure(
            id="MSD_FOOTNOTES", maintainer="STATCAN",
            attributes=[sl.MetadataAttribute(id="FOOTNOTE_TEXT", ...)],
        )
        msd.attributes["FOOTNOTE_TEXT"]  # a root attribute by id
        msd["CONTACT.EMAIL"]             # any attribute by path
    """

    sdmx_package: ClassVar[str] = "metadatastructure"
    sdmx_class: ClassVar[str] = "MetadataStructure"
    msg_field: ClassVar[str] = "metadata_structures"

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
    attributes: ItemList[MetadataAttribute] = field(factory=ItemList, converter=to_itemlist)
    #: The annotations of the list around `attributes` — 3.x's
    #: `MetadataAttributeList`, which this model does not reify (#422).
    attribute_list_annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: The attribute list's ``@uri`` (#422).
    attribute_list_uri: str | None = None
    #: The attribute list's ``<com:Link>`` elements (#422).
    attribute_list_links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX-ML 2.1's reporting layer, absent from 3.0. Populated when reading
    #: 2.1 and used when writing it; ignored by every other target, which has
    #: no such concept. The attributes themselves stay in `attributes` — a
    #: report structure references them by id (#245).
    targets: tuple[MetadataTarget, ...] = field(factory=tuple, converter=tuple)
    report_structures: tuple[ReportStructure, ...] = field(factory=tuple, converter=tuple)
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
    # Set by `__attrs_post_init__`, so no default: the index is a function of
    # the tree field and has no meaning before it.
    _tree: PathIndex[MetadataAttribute] = field(init=False, repr=False, eq=False, metadata=derived_view())

    def ref_to(self, path: str) -> Ref[MetadataAttribute]:
        """Build a resolved `Ref` to a metadata attribute within this structure, by path.

        A nested attribute is named by its dotted path, which is also the item
        part of its URN: ``msd.ref_to("CONTACT.EMAIL")``.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist — which is what six hand-written
        keyword arguments to `SdmxUrn.make` invite, and what
        `validate_references` now correctly reports as broken.

        Raises:
            KeyError: If no metadata attribute has this path in this structure.

        Example:
            msd.ref_to("CONTACT.EMAIL")
        """
        return item_ref(self, path, MetadataAttribute)

    def __attrs_post_init__(self) -> None:
        # Construction, not mutation: the one write a frozen class makes to itself (#492).
        object.__setattr__(self, "_tree", PathIndex(self.attributes, lambda a: a.children))
        # Its whole-artefact rules, once the index they read is built (#492).
        enforce(self)

    @property
    @override
    def _paths(self) -> PathIndex[MetadataAttribute]:
        return self._tree

    # ── SKOS/XKOS hierarchy navigation ────────────────────────────────────
    # Path-keyed, from `PathNavigation`; `roots` is this structure's own field.

    def roots(self) -> ItemList[MetadataAttribute]:
        """Top-level attributes (no parent)."""
        return self.attributes

    def add(self, attributes: MetadataAttribute | list[MetadataAttribute]) -> MetadataStructure:
        """Return a new MetadataStructure with *attributes* added."""
        new = [attributes] if isinstance(attributes, MetadataAttribute) else list(attributes)
        return evolve(self, attributes=[*self.attributes, *new])

    def drop(self, attribute_ids: str | list[str]) -> MetadataStructure:
        """Return a new MetadataStructure with the given attribute(s) removed.

        Searches only the top-level attributes by id (children are not flattened).
        Raises KeyError if any id is not present.
        """
        ids = [attribute_ids] if isinstance(attribute_ids, str) else list(attribute_ids)
        missing = [i for i in ids if i not in self.attributes]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(self, attributes=[a for a in self.attributes if a.id not in id_set])
