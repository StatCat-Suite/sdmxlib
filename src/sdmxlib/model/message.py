"""StructureMessage and MetadataMessage — wire-format envelopes.

The wire formats split into two distinct envelopes:

- ``StructureMessage`` carries definitions (Codelists, DSDs, MSDs,
  Metadataflows, …). Maps to ``<mes:Structure>`` in SDMX-ML 3.0 and to
  the structure ``$schema`` in SDMX-JSON 2.0.
- ``MetadataMessage`` carries populated reference metadata
  (``MetadataSet`` instances). Maps to ``<mes:GenericMetadata>`` in
  SDMX-ML 3.0 and to the metadata ``$schema`` in SDMX-JSON 2.0.

The format layer auto-discriminates inbound messages and dispatches
outbound writes on the message type.
"""

from collections.abc import Callable, Iterable
from typing import Any, ClassVar

from attrs import field, frozen

from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import Categorisation, CategoryScheme
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.collections import ArtefactList, Maintainable
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import AttachmentConstraint, AvailabilityConstraint, DataConstraint
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import ReadAssumption, ReadGap, Refusal
from sdmxlib.model.header import Header, MetadataHeader
from sdmxlib.model.hierarchy import HierarchicalCodelist, Hierarchy, HierarchyAssociation
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    OrganisationSchemeMap,
    RepresentationMap,
    StructureMap,
    StructureSet,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import (
    AgencyScheme,
    DataConsumerScheme,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.valuelist import ValueList


def _to_artefact_list[T: Maintainable](v: ArtefactList[T] | list[T]) -> ArtefactList[T]:
    """Attrs converter: wrap a list in ArtefactList, or pass one through.

    Mirrors `collections.to_itemlist`; generic so the element type survives
    into the synthesized ``__init__`` instead of degrading to ``Any``.
    """
    return v if isinstance(v, ArtefactList) else ArtefactList(v)


def _to_lost(
    value: Iterable[ReadGap] | Callable[[], tuple[ReadGap, ...]],
) -> tuple[ReadGap, ...] | Callable[[], tuple[ReadGap, ...]]:
    """Losses as a tuple, or the function that finds them left as it is (#461)."""
    return value if callable(value) else tuple(value)


@frozen(unsafe_hash=False)
class StructureMessage:
    """Wire-format envelope for SDMX structural definitions.

    Carries Codelists, DSDs, MSDs, Metadataflows, etc. Cross-references
    within the message are auto-resolved by the reader.

    Example:
        msg = parse(xml_bytes)
        dsd = msg.data_structures[0]
        cl = msg.codelists["SDMX:CL_FREQ(1.0)"]
    """

    #: Not hashable, declared rather than discovered (#290).
    #:
    #: `@frozen` makes attrs generate a `__hash__`, and it worked — for an
    #: *empty* message. Every collection field holds attrs `@define` items,
    #: which are mutable and so unhashable, so any message carrying anything
    #: raised. `unsafe_hash=False` stops attrs supplying one, and `None` says
    #: so at the class rather than at the point of use.
    #:
    #: `@frozen` is here for immutability, which still holds; hashability was
    #: only ever its side effect. A caller who asks to hash a message is now
    #: told about the message, instead of about an `ArtefactList` they never
    #: mentioned.
    # `__hash__ = None` is the documented Python idiom for "not hashable" —
    # CPython, `dataclasses` and `attrs` all use it — and it is the *only*
    # spelling that makes `isinstance(x, Hashable)` answer False. basedpyright
    # rejected every form of it, even a fully annotated
    # `ClassVar[Callable[..., int] | None]`, and needed a suppression here until
    # it was retired (#425); pyrefly accepts it. The alternative — a `__hash__`
    # that raises — passes the checker and lies at runtime:
    # `isinstance(x, Hashable)` becomes True, so code that feature-detects
    # hashability gets the wrong answer and fails later instead of earlier.
    __hash__: ClassVar[None] = None

    codelists: ArtefactList[Codelist] = field(factory=ArtefactList, converter=_to_artefact_list)
    #: SDMX 3.x only — 2.1 has no ValueList, and leaves one out (#422).
    value_lists: ArtefactList[ValueList] = field(factory=ArtefactList, converter=_to_artefact_list)
    concept_schemes: ArtefactList[ConceptScheme] = field(factory=ArtefactList, converter=_to_artefact_list)
    data_structures: ArtefactList[DataStructure] = field(factory=ArtefactList, converter=_to_artefact_list)
    dataflows: ArtefactList[Dataflow] = field(factory=ArtefactList, converter=_to_artefact_list)
    category_schemes: ArtefactList[CategoryScheme] = field(factory=ArtefactList, converter=_to_artefact_list)
    categorisations: ArtefactList[Categorisation] = field(factory=ArtefactList, converter=_to_artefact_list)
    provision_agreements: ArtefactList[ProvisionAgreement] = field(factory=ArtefactList, converter=_to_artefact_list)
    agency_schemes: ArtefactList[AgencyScheme] = field(factory=ArtefactList, converter=_to_artefact_list)
    data_provider_schemes: ArtefactList[DataProviderScheme] = field(factory=ArtefactList, converter=_to_artefact_list)
    #: SDMX 3.0 only — 2.1 has no MetadataProviderScheme (#315).
    metadata_provider_schemes: ArtefactList[MetadataProviderScheme] = field(
        factory=ArtefactList, converter=_to_artefact_list
    )
    data_consumer_schemes: ArtefactList[DataConsumerScheme] = field(factory=ArtefactList, converter=_to_artefact_list)
    organisation_unit_schemes: ArtefactList[OrganisationUnitScheme] = field(
        factory=ArtefactList, converter=_to_artefact_list
    )
    constraints: ArtefactList[DataConstraint] = field(factory=ArtefactList, converter=_to_artefact_list)
    attachment_constraints: ArtefactList[AttachmentConstraint] = field(
        factory=ArtefactList, converter=_to_artefact_list
    )
    #: SDMX 3.1's availability statements (#421). Not artefacts — no id,
    #: agency or version — so held here as plain values, in no artefact list,
    #: and absent from `all_artefacts`. Only SDMX-ML 3.1 writes them; every
    #: other target, the store included, leaves them out (`formats.gaps.dropped`).
    availability_constraints: tuple[AvailabilityConstraint, ...] = field(factory=tuple, converter=tuple)
    representation_maps: ArtefactList[RepresentationMap] = field(factory=ArtefactList, converter=_to_artefact_list)
    structure_maps: ArtefactList[StructureMap] = field(factory=ArtefactList, converter=_to_artefact_list)
    concept_scheme_maps: ArtefactList[ConceptSchemeMap] = field(factory=ArtefactList, converter=_to_artefact_list)
    category_scheme_maps: ArtefactList[CategorySchemeMap] = field(factory=ArtefactList, converter=_to_artefact_list)
    organisation_scheme_maps: ArtefactList[OrganisationSchemeMap] = field(
        factory=ArtefactList, converter=_to_artefact_list
    )
    hierarchies: ArtefactList[Hierarchy] = field(factory=ArtefactList, converter=_to_artefact_list)
    #: SDMX-ML 2.1 containers. Both group artefacts that are top-level in this
    #: model, and both are maintainables in their own right, so they are
    #: modelled rather than synthesised — see `StructureSet`. Written only to
    #: 2.1; the other targets have no such concept and `validation` refuses
    #: them there (#239).
    structure_sets: ArtefactList[StructureSet] = field(factory=ArtefactList, converter=_to_artefact_list)
    hierarchical_codelists: ArtefactList[HierarchicalCodelist] = field(
        factory=ArtefactList, converter=_to_artefact_list
    )
    hierarchy_associations: ArtefactList[HierarchyAssociation] = field(
        factory=ArtefactList, converter=_to_artefact_list
    )
    metadata_structures: ArtefactList[MetadataStructure] = field(factory=ArtefactList, converter=_to_artefact_list)
    metadataflows: ArtefactList[Metadataflow] = field(factory=ArtefactList, converter=_to_artefact_list)
    #: SDMX 3.0's reference-metadata analogue of `ProvisionAgreement`. Modelled
    #: and storable since it was added, but with no slot here until #362 — so
    #: `containing()` raised for it and no structure reader could produce one.
    metadata_provision_agreements: ArtefactList[MetadataProvisionAgreement] = field(
        factory=ArtefactList, converter=_to_artefact_list
    )
    #: The message's header (#480), read through `header` and set as
    #: ``header=``. Private for the reason `_refused` is: it is not an artefact.
    _header: Header | None = None
    #: Artefacts the reader declined to build (#435), read through `refused`.
    #: Private so the code that walks `attrs.fields(StructureMessage)` as the
    #: list of artefact slots — the coverage guards, the store round trip —
    #: keeps meaning that: a refusal is not an artefact, and no writer or
    #: store has anywhere to put one. attrs still accepts it as ``refused=``.
    _refused: tuple[Refusal, ...] = field(factory=tuple, converter=tuple)
    #: Content a reader dropped from artefacts it did build (#422), read
    #: through `lost`. Private for the reason `_refused` is. Either the losses
    #: or how to find them: the SDMX-JSON reader's search walks every key of
    #: the document, 40% of its read, so it is left until `lost` is read
    #: (#461). Not compared: a loss describes how a document was read, not
    #: what the message holds.
    _lost: tuple[ReadGap, ...] | Callable[[], tuple[ReadGap, ...]] = field(factory=tuple, converter=_to_lost, eq=False)

    #: Values a reader filled in for required content the document omitted
    #: (#474), read through `assumed`. Private for the reason `_refused` is;
    #: not compared, for the reason `_lost` is not: it describes the read.
    _assumed: tuple[ReadAssumption, ...] = field(factory=tuple, converter=tuple, eq=False)

    @property
    def refused(self) -> tuple[Refusal, ...]:
        """Artefacts the reader refused, each with the reason (#435).

        A document is read artefact by artefact: one that contradicts the
        schema is recorded here and the rest are read. Empty for a message
        built in code, or read from a document with nothing wrong in it.
        ``parse()`` raises on a non-empty one unless called with
        ``strict=False``.
        """
        return self._refused

    @property
    def lost(self) -> tuple[ReadGap, ...]:
        """What the reader dropped from artefacts it built, per artefact and construct (#422).

        SDMX 3.x carries constructs the model has no field for yet — a
        `com:Link`, a DSD's `MetadataAttributeUsage`. The artefact is built
        without them and each loss is recorded here, so a read is never
        quietly smaller than its document. Empty for a message built in code.
        ``parse(..., lossless=True)`` raises on a non-empty one.

        An SDMX-JSON read works its losses out here, on first access, rather
        than during the read (#461). Finding them walks the whole document, and
        most callers never ask, so a read that walked eagerly cost them 40-90%
        more. The price is memory: until `lost` is first read, the message
        holds its source document's bytes, at most one copy, the size of what
        was parsed (#473). Reading `lost`, or parsing with
        ``parse(..., lossless=True)``, which reads it, releases them.
        """
        lost = self._lost
        if callable(lost):
            lost = lost()
            # @frozen blocks assignment; the result is fixed for this message's
            # lifetime, as `SdmxUrn.__str__`'s cache is.
            object.__setattr__(self, "_lost", lost)
        return lost

    @property
    def assumed(self) -> tuple[ReadAssumption, ...]:
        """Values the reader supplied where the document omitted something the schema requires (#474).

        Beside `refused` (artefacts not built) and `lost` (content dropped), the
        third account of a read: content *filled in*. IMF omits the required
        ``AttributeRelationship`` on thousands of attribute usages, and the
        reader takes each to be dataset-level, a tolerance chosen in #421; this
        is where that choice shows. Empty for a message built in code.
        ``parse(..., lossless=True)`` raises on a non-empty one.
        """
        return self._assumed

    @property
    def header(self) -> Header | None:
        """Who sent the message, to whom, when and what about; None when it has none (#480)."""
        return self._header

    def all_artefacts(self) -> list[MaintainableArtefact]:
        """Flat list of every artefact in the message — all 24 slots.

        **Every** slot, which it did not used to be: `structure_sets` and
        `hierarchical_codelists` were absent, and the two callers of this
        method want opposite things from it (#394).

        *Validation* wants everything, and silently got 21 of 23 — so
        `validate_references` never checked a `StructureSet` or a
        `HierarchicalCodelist`, which between them carry four of the five
        container-of-`Ref` fields in the model.

        *Storage* wants only what it can persist, and this is not that list:
        four of the 21 it already yielded have no `ArtefactKind` (#395), so
        `add_all(msg.all_artefacts())` refuses them. Completing the list makes
        that six.

        Completing it is still right, because the alternative was worse than a
        refusal. Ingesting a message through `parse(xml, registry=…)` dropped
        the two absent artefacts **silently** — a `HierarchicalCodelist` named
        "Geographic hierarchy" simply ceased to exist, which is the name #239
        added the container to stop losing. `formats/gaps.py` states the rule
        this follows: *dropping is the one that burns an author; a refusal is
        visible, a drop is not.*

        `tests/unit/test_domain_model.py` compares this against the class's
        own fields, so a new slot cannot be added without a decision. One is
        left out on purpose: `availability_constraints`, SDMX 3.1's
        availability statements, which have no identity and so are not
        artefacts — nothing can store, reference or validate one by URN (#421).
        """
        return [
            *self.codelists,
            *self.value_lists,
            *self.concept_schemes,
            *self.data_structures,
            *self.dataflows,
            *self.category_schemes,
            *self.categorisations,
            *self.provision_agreements,
            *self.agency_schemes,
            *self.data_provider_schemes,
            *self.metadata_provider_schemes,
            *self.data_consumer_schemes,
            *self.organisation_unit_schemes,
            *self.constraints,
            *self.attachment_constraints,
            *self.representation_maps,
            *self.structure_maps,
            *self.concept_scheme_maps,
            *self.category_scheme_maps,
            *self.organisation_scheme_maps,
            *self.hierarchies,
            *self.hierarchy_associations,
            *self.metadata_structures,
            *self.metadataflows,
            *self.metadata_provision_agreements,
            *self.structure_sets,
            *self.hierarchical_codelists,
        ]

    @classmethod
    def containing(cls, artefact: object) -> StructureMessage:
        """Wrap a single structural artefact in a fresh `StructureMessage`.

        Dispatches on the artefact's runtime type to the matching
        collection slot (``Codelist`` → ``codelists``, ``Dataflow`` →
        ``dataflows``, …) using the artefact class's ``msg_field``
        ClassVar. Saves REST handlers from a long ``isinstance`` chain.

        Example:
            return StructureMessage.containing(dataflow)
            # equivalent to: StructureMessage(dataflows=[dataflow])

        A lazy artefact is materialised first. `LocalRegistry.get` returns a
        `LazyCodelist`, so a `/structure` handler reading from a local store
        hands one straight to this method — and a message holding a lazy
        object would go stale the moment its registry closed, which is after
        the handler returns and before the bytes are written.

        Args:
            artefact: A maintainable artefact whose type maps to one of
                this message's slots.

        Raises:
            TypeError: If the artefact's class has no ``msg_field`` or
                its ``msg_field`` is not a slot on
                `StructureMessage`.
        """
        # Duck-typed rather than an isinstance against `storage.lazy`: the
        # model layer imports nothing from storage, and "can make itself
        # eager" is the whole contract this needs.
        materialize = getattr(artefact, "materialize", None)
        if callable(materialize):
            artefact = materialize()
        slot = getattr(type(artefact), "msg_field", None)
        if not isinstance(slot, str):
            msg = (
                f"StructureMessage.containing: {type(artefact).__name__} has no "
                "msg_field — not a known maintainable artefact"
            )
            raise TypeError(msg)
        valid_slots = {a.name for a in cls.__attrs_attrs__ if not a.name.startswith("_")}  # pyrefly: ignore[missing-attribute]
        if slot not in valid_slots:
            msg = (
                f"StructureMessage.containing: {type(artefact).__name__} maps to "
                f"slot {slot!r} which is not a StructureMessage field "
                f"(valid: {sorted(valid_slots)})"
            )
            raise TypeError(msg)
        # The slot name is only known at runtime, so the keyword expansion
        # can't be checked statically. Confine that to one annotated local
        # rather than widening the parameter to Any.
        kwargs: dict[str, Any] = {slot: [artefact]}
        return cls(**kwargs)


@frozen(unsafe_hash=False)
class MetadataMessage:
    """Wire-format envelope for populated reference metadata.

    Carries MetadataSet instances. Maps to ``<mes:GenericMetadata>`` in
    SDMX-ML 3.0 and to the metadata ``$schema`` envelope in SDMX-JSON 2.0.

    Example:
        msg = parse(metadata_xml_bytes)
        for ms in msg.metadata_sets:
            for target in ms.targets:
                print(target.urn, ms.attributes.ids())
    """

    #: Not hashable, declared rather than discovered (#290).
    #:
    #: `@frozen` makes attrs generate a `__hash__`, and it worked — for an
    #: *empty* message. Every collection field holds attrs `@define` items,
    #: which are mutable and so unhashable, so any message carrying anything
    #: raised. `unsafe_hash=False` stops attrs supplying one, and `None` says
    #: so at the class rather than at the point of use.
    #:
    #: `@frozen` is here for immutability, which still holds; hashability was
    #: only ever its side effect. A caller who asks to hash a message is now
    #: told about the message, instead of about an `ArtefactList` they never
    #: mentioned.
    # `__hash__ = None` is the documented Python idiom for "not hashable" —
    # CPython, `dataclasses` and `attrs` all use it — and it is the *only*
    # spelling that makes `isinstance(x, Hashable)` answer False. basedpyright
    # rejected every form of it, even a fully annotated
    # `ClassVar[Callable[..., int] | None]`, and needed a suppression here until
    # it was retired (#425); pyrefly accepts it. The alternative — a `__hash__`
    # that raises — passes the checker and lies at runtime:
    # `isinstance(x, Hashable)` becomes True, so code that feature-detects
    # hashability gets the wrong answer and fails later instead of earlier.
    __hash__: ClassVar[None] = None

    metadata_sets: ArtefactList[MetadataSet] = field(factory=ArtefactList, converter=_to_artefact_list)
    #: The message's header (#480), read through `header` and set as ``header=``.
    _header: MetadataHeader | None = None

    @property
    def header(self) -> MetadataHeader | None:
        """Who sent the message, to whom, when and what about; None when it has none (#480)."""
        return self._header

    def all_artefacts(self) -> list[MaintainableArtefact]:
        """Flat list of every artefact in the message."""
        return list(self.metadata_sets)
