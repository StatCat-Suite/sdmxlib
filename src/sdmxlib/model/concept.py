"""ConceptScheme and Concept — semantic meaning of components."""

from datetime import datetime
from typing import ClassVar

from attrs import evolve, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._items import item_ref
from sdmxlib.model._rules import id_type, maintained_by, named, ncname_id_type, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ItemList, to_itemlist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text
from sdmxlib.model.representation import Facet, Representation, to_text_representation
from sdmxlib.model.urn import ParsesUrn, SdmxUrn


@frozen
class ISOConceptReference:
    """A concept's counterpart in an ISO 11179 registry — SDMX's ``ISOConceptReference`` (#422).

    Three plain strings, as the schemas spell them: a reference *out* of SDMX,
    so not a `Ref`, which names SDMX artefacts. sdmx1 models it the same way.
    """

    agency: str
    concept_scheme_id: str
    concept_id: str


@frozen(unsafe_hash=False)
class Concept(ParsesUrn):
    """The semantic meaning of a DSD component (e.g. "Frequency", "Reference Area")."""

    #: What the SDMX-ML schema's ``fixed=`` supplies when a producer omits
    #: ``@package`` / ``@class`` on a ``<Ref>`` (#240). Declared on the *item*
    #: class, matching how maintainables carry theirs, so `resolve_ref` can
    #: fill in what the wire left out rather than dropping the reference.
    #:
    #: The pair describes the **item**, not its container: a concept URN is
    #: ``…conceptscheme.Concept=AG:CS(1.0).CONTACT``, so the class is `Concept`
    #: while the id path names the scheme.
    sdmx_package: ClassVar[str] = "conceptscheme"
    sdmx_class: ClassVar[str] = "Concept"

    id: str = field(validator=id_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    core_representation: Representation = field(
        default=None, converter=to_text_representation, validator=refuse_urn_text
    )
    #: The concept this one qualifies, within the same scheme — SDMX-ML's
    #: ``<str:Parent>``, which the XSD describes as "the semantic relationships
    #: between concepts which occur within a single concept scheme". A *flat*
    #: reference like `Code.parent_id`, not nesting like `Category`: the wire
    #: declares every concept at scheme level and points upwards.
    parent_id: str | None = None
    #: The facet constraining the codes of an enumerated `core_representation`
    #: — the ``<CoreRepresentation>``'s ``<EnumerationFormat>``, as a
    #: component's `enumeration_format` is its local representation's (#422).
    enumeration_format: Facet | None = None
    #: SDMX ``<ISOConceptReference>`` (#422).
    iso_concept_reference: ISOConceptReference | None = None
    #: The core representation's ``minOccurs`` / ``maxOccurs`` (SDMX 3.x,
    #: #422): how many values a component taking this concept's
    #: representation may report, as an `Attribute`'s; None is unbounded.
    min_occurs: int = 1
    max_occurs: int | None = 1
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)

    @classmethod
    def urn_for(
        cls,
        *,
        agency: str,
        scheme: str,
        id: str,
        version: str = "latest",
    ) -> SdmxUrn[Concept]:
        """Build an item-level URN for a Concept within a ConceptScheme.

        Hides the SDMX package/class internals. Maps the caller's intuitive
        ``scheme`` / ``id`` parameters to the URN's ``id`` / ``item_id`` fields.

        Example:
            Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ")
            # urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=SDMX:CROSS_DOMAIN_CONCEPTS(latest).FREQ
        """
        return SdmxUrn.make(
            Concept,
            package="conceptscheme",
            artefact_class="Concept",
            agency=agency,
            id=scheme,
            version=version,
            item_id=id,
        )


@frozen(unsafe_hash=False)
class ConceptScheme(MaintainableArtefact):
    """An ordered collection of Concepts maintained by an agency."""

    sdmx_package: ClassVar[str] = "conceptscheme"
    sdmx_class: ClassVar[str] = "ConceptScheme"
    msg_field: ClassVar[str] = "concept_schemes"

    id: str = field(validator=ncname_id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    concepts: ItemList[Concept] = field(factory=ItemList, converter=to_itemlist)
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
    #: SDMX ``@isPartial``: the scheme holds a subset of its items (#422).
    is_partial: bool = False

    def add(self, concepts: Concept | list[Concept]) -> ConceptScheme:
        """Return a new ConceptScheme with *concepts* added.

        Accepts a single Concept or a list of Concepts.
        """
        new = [concepts] if isinstance(concepts, Concept) else list(concepts)
        return evolve(self, concepts=[*self.concepts, *new])

    def drop(self, concept_ids: str | list[str]) -> ConceptScheme:
        """Return a new ConceptScheme with the given concept(s) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        ids = [concept_ids] if isinstance(concept_ids, str) else list(concept_ids)
        missing = [i for i in ids if i not in self.concepts]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(self, concepts=[c for c in self.concepts if c.id not in id_set])

    def ref_to(self, concept_id: str) -> Ref[Concept]:
        """Build a resolved Ref to a concept within this scheme.

        Raises KeyError if concept_id is not present.

        One of seven now — `Codelist`, `CategoryScheme`, the three organisation
        schemes, `MetadataStructure` and `DataStructure` all have one, on the
        shared `_items.item_ref` path rather than a copy of `SdmxUrn.make` each
        (#407). This was the only one for a long time, which is why authoring a
        3.0 `Hierarchy` meant building item URNs by hand.

        Example:
            cs = ConceptScheme(id="CS_COMMON", maintainer="SDMX", version="1.0",
                               concepts=ItemList([Concept(id="FREQ")]))
            ref = cs.ref_to("FREQ")
            ref()  # Concept(id="FREQ", ...)
        """
        return item_ref(self, concept_id, Concept)

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)
