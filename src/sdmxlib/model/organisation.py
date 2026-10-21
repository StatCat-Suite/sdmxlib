"""Organisation types: agencies, providers, data consumers and organisation units, and their schemes."""

from datetime import datetime
from typing import ClassVar

from attrs import evolve, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._items import item_ref
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ItemList
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.istring import to_istring as _to_istring
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import ParsesUrn, SdmxUrn


def _to_annotations(v: Annotations | list[Annotation]) -> Annotations:
    return Annotations(v) if isinstance(v, list) else v


@frozen(unsafe_hash=False)
class Contact:
    """Contact information for an agency or data provider."""

    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    department: InternationalString = field(factory=InternationalString, converter=_to_istring)
    role: InternationalString = field(factory=InternationalString, converter=_to_istring)
    phones: tuple[str, ...] = field(factory=tuple, converter=tuple)
    faxes: tuple[str, ...] = field(factory=tuple, converter=tuple)
    #: X.400 messaging addresses per the SDMX ``ContactType.X400`` element.
    #: Rare in practice but valid per every SDMX-ML XSD.
    x400s: tuple[str, ...] = field(factory=tuple, converter=tuple)
    uris: tuple[str, ...] = field(factory=tuple, converter=tuple)
    emails: tuple[str, ...] = field(factory=tuple, converter=tuple)
    #: SDMX ``@id``: a user id for the contact (#422).
    id: str | None = None


@frozen(unsafe_hash=False)
class Agency(ParsesUrn):
    """An organisation that maintains SDMX artefacts."""

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: Every organisation sits in the ``base`` package, whatever scheme holds
    #: it.
    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "Agency"

    id: str
    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    contacts: tuple[Contact, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


def _to_agency(v: Agency | str) -> Agency:
    return Agency(id=v) if isinstance(v, str) else v


def _to_item_list_agency(v: ItemList[Agency] | list[Agency]) -> ItemList[Agency]:
    return v if isinstance(v, ItemList) else ItemList(v)


def _to_item_list_provider(v: ItemList[DataProvider] | list[DataProvider]) -> ItemList[DataProvider]:
    return v if isinstance(v, ItemList) else ItemList(v)


def _to_item_list_metadata_provider(
    v: ItemList[MetadataProvider] | list[MetadataProvider],
) -> ItemList[MetadataProvider]:
    return v if isinstance(v, ItemList) else ItemList(v)


def _to_item_list_consumer(v: ItemList[DataConsumer] | list[DataConsumer]) -> ItemList[DataConsumer]:
    return v if isinstance(v, ItemList) else ItemList(v)


def _to_item_list_unit(v: ItemList[OrganisationUnit] | list[OrganisationUnit]) -> ItemList[OrganisationUnit]:
    return v if isinstance(v, ItemList) else ItemList(v)


@frozen(unsafe_hash=False)
class DataProvider(ParsesUrn):
    """An organisation that provides data under a ProvisionAgreement."""

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "DataProvider"

    id: str = field(validator=id_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    contacts: tuple[Contact, ...] = field(factory=tuple, converter=tuple)
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
        id: str,
        scheme: str = "DATA_PROVIDERS",
        version: str = "latest",
    ) -> SdmxUrn[DataProvider]:
        """Build an item-level URN for a DataProvider within its scheme.

        Hides the package/class internals. Building one by concatenating
        ``.id`` onto the *scheme's* urn produces a urn whose class is
        ``DataProviderScheme``, which then resolves against nothing —
        an easy mistake, and a silent one (#315).

        `scheme` defaults to ``"DATA_PROVIDERS"`` because the XSD pins the
        scheme's id to that one literal — see
        `validation._FIXED_SCHEME_IDS` — so the caller almost never has a
        choice to make.

        Example:
            DataProvider.urn_for(agency="SDMX", id="P1")
        """
        return SdmxUrn.make(
            DataProvider,
            package="base",
            artefact_class="DataProvider",
            agency=agency,
            id=scheme,
            version=version,
            item_id=id,
        )


@frozen(unsafe_hash=False)
class MetadataProvider(ParsesUrn):
    """An organisation that provides reference metadata under an MPA — #315.

    The metadata twin of `DataProvider`, and identical in shape: both are
    `Organisation` in the IM, so both carry `contacts`.

    SDMX **3.0** surface. 2.1 has no `MetadataProviderScheme` at all — verified
    against the vendored schemas, where it appears in the 3.0 and 3.1
    `SDMXStructureOrganisation.xsd` and in neither 2.1 file — so writing 2.1
    refuses the scheme rather than dropping it (`validation._UNWRITABLE`).
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "MetadataProvider"

    id: str
    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    contacts: tuple[Contact, ...] = field(factory=tuple, converter=tuple)
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
        id: str,
        scheme: str = "METADATA_PROVIDERS",
        version: str = "latest",
    ) -> SdmxUrn[MetadataProvider]:
        """Build an item-level URN for a MetadataProvider within its scheme.

        The metadata twin. Same trap it exists to avoid: concatenating ``.id``
        onto the scheme's urn yields class ``MetadataProviderScheme``,
        and the join then matches no rows without erroring (#315).

        `scheme` defaults to ``"METADATA_PROVIDERS"`` because the XSD pins the
        scheme's id to that one literal — see
        `validation._FIXED_SCHEME_IDS` — so the caller almost never has a
        choice to make.

        Example:
            MetadataProvider.urn_for(agency="SDMX", id="P1")
        """
        return SdmxUrn.make(
            MetadataProvider,
            package="base",
            artefact_class="MetadataProvider",
            agency=agency,
            id=scheme,
            version=version,
            item_id=id,
        )


@frozen(unsafe_hash=False)
class DataConsumer(ParsesUrn):
    """An organisation that consumes data: the receiving counterpart of `DataProvider` (#469).

    Identical in shape to `DataProvider`, as both are `Organisation` in the IM.
    Held in a `DataConsumerScheme`, whose id every format pins to
    ``DATA_CONSUMERS``.
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "DataConsumer"

    id: str = field(validator=id_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    contacts: tuple[Contact, ...] = field(factory=tuple, converter=tuple)
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
        id: str,
        scheme: str = "DATA_CONSUMERS",
        version: str = "latest",
    ) -> SdmxUrn[DataConsumer]:
        """Build an item-level URN for a DataConsumer within its scheme.

        The same trap as `DataProvider.urn_for`: concatenating ``.id`` onto the
        scheme's urn yields class ``DataConsumerScheme``, which resolves
        against nothing (#315).

        Example:
            DataConsumer.urn_for(agency="SDMX", id="C1")
        """
        return SdmxUrn.make(
            DataConsumer,
            package="base",
            artefact_class="DataConsumer",
            agency=agency,
            id=scheme,
            version=version,
            item_id=id,
        )


@frozen(unsafe_hash=False)
class OrganisationUnit(ParsesUrn):
    """A unit within an organisation, such as a department or division (#469).

    Hierarchical within its scheme: `parent_id` names another unit of the same
    `OrganisationUnitScheme`, as `Code.parent_id` names a code. That is how
    every format writes it: 2.1 as `<Parent><Ref id>`, 3.x as `<Parent>` text,
    and SDMX-JSON as `parent`.
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "OrganisationUnit"

    id: str = field(validator=id_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    contacts: tuple[Contact, ...] = field(factory=tuple, converter=tuple)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    parent_id: str | None = None
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


@frozen(unsafe_hash=False)
class AgencyScheme(MaintainableArtefact):
    """Maintainable container for Agency items."""

    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "AgencyScheme"
    msg_field: ClassVar[str] = "agency_schemes"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    agencies: ItemList[Agency] = field(factory=ItemList, converter=_to_item_list_agency)
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

    def ref_to(self, agency_id: str) -> Ref[Agency]:
        """Build a resolved `Ref` to a agency within this scheme.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist — which is what six hand-written
        keyword arguments to `SdmxUrn.make` invite, and what
        `validate_references` now correctly reports as broken (#407).

        Raises:
            KeyError: If no agency with this id is in this scheme.

        Example:
            scheme.ref_to("ESTAT")
        """
        return item_ref(self, agency_id, Agency)

    def add(self, agencies: Agency | list[Agency]) -> AgencyScheme:
        """Return a new AgencyScheme with *agencies* added.

        Accepts a single Agency or a list of Agencies.
        """
        new = [agencies] if isinstance(agencies, Agency) else list(agencies)
        return evolve(self, agencies=[*self.agencies, *new])

    def drop(self, agency_ids: str | list[str]) -> AgencyScheme:
        """Return a new AgencyScheme with the given agency(ies) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        ids = [agency_ids] if isinstance(agency_ids, str) else list(agency_ids)
        missing = [i for i in ids if i not in self.agencies]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(self, agencies=[a for a in self.agencies if a.id not in id_set])

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


@frozen(unsafe_hash=False)
class DataProviderScheme(MaintainableArtefact):
    """Maintainable container for DataProvider items."""

    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "DataProviderScheme"
    msg_field: ClassVar[str] = "data_provider_schemes"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    providers: ItemList[DataProvider] = field(factory=ItemList, converter=_to_item_list_provider)
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

    def ref_to(self, provider_id: str) -> Ref[DataProvider]:
        """Build a resolved `Ref` to a provider within this scheme.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist — which is what six hand-written
        keyword arguments to `SdmxUrn.make` invite, and what
        `validate_references` now correctly reports as broken (#407).

        Raises:
            KeyError: If no provider with this id is in this scheme.

        Example:
            scheme.ref_to("ECB")
        """
        return item_ref(self, provider_id, DataProvider)

    def add(self, providers: DataProvider | list[DataProvider]) -> DataProviderScheme:
        """Return a new DataProviderScheme with *providers* added.

        Accepts a single DataProvider or a list of DataProviders.
        """
        new = [providers] if isinstance(providers, DataProvider) else list(providers)
        return evolve(self, providers=[*self.providers, *new])

    def drop(self, provider_ids: str | list[str]) -> DataProviderScheme:
        """Return a new DataProviderScheme with the given provider(s) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        ids = [provider_ids] if isinstance(provider_ids, str) else list(provider_ids)
        missing = [i for i in ids if i not in self.providers]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(self, providers=[p for p in self.providers if p.id not in id_set])

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


@frozen(unsafe_hash=False)
class MetadataProviderScheme(MaintainableArtefact):
    """Maintainable container for MetadataProvider items — #315.

    Mirrors `DataProviderScheme`. Until this existed,
    `MetadataProvisionAgreement.metadata_provider` was a `Ref[object]` and the
    stored `metadata_provider_urn` column pointed at a relation that did not
    exist: populated, and unresolvable by anything.
    """

    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "MetadataProviderScheme"
    msg_field: ClassVar[str] = "metadata_provider_schemes"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    providers: ItemList[MetadataProvider] = field(factory=ItemList, converter=_to_item_list_metadata_provider)
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

    def ref_to(self, provider_id: str) -> Ref[MetadataProvider]:
        """Build a resolved `Ref` to a provider within this scheme.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist — which is what six hand-written
        keyword arguments to `SdmxUrn.make` invite, and what
        `validate_references` now correctly reports as broken (#407).

        Raises:
            KeyError: If no provider with this id is in this scheme.

        Example:
            scheme.ref_to("ECB")
        """
        return item_ref(self, provider_id, MetadataProvider)

    def add(self, providers: MetadataProvider | list[MetadataProvider]) -> MetadataProviderScheme:
        """Return a new MetadataProviderScheme with *providers* added.

        Accepts a single MetadataProvider or a list of them.
        """
        new = [providers] if isinstance(providers, MetadataProvider) else list(providers)
        return evolve(self, providers=[*self.providers, *new])

    def drop(self, provider_ids: str | list[str]) -> MetadataProviderScheme:
        """Return a new MetadataProviderScheme with the given provider(s) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        ids = [provider_ids] if isinstance(provider_ids, str) else list(provider_ids)
        missing = [i for i in ids if i not in self.providers]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(self, providers=[p for p in self.providers if p.id not in id_set])

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


@frozen(unsafe_hash=False)
class DataConsumerScheme(MaintainableArtefact):
    """Maintainable container for DataConsumer items (#469). Its id is fixed to ``DATA_CONSUMERS``."""

    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "DataConsumerScheme"
    msg_field: ClassVar[str] = "data_consumer_schemes"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    consumers: ItemList[DataConsumer] = field(factory=ItemList, converter=_to_item_list_consumer)
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

    def ref_to(self, consumer_id: str) -> Ref[DataConsumer]:
        """Build a resolved `Ref` to a consumer within this scheme.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist (#407).

        Raises:
            KeyError: If no consumer with this id is in this scheme.
        """
        return item_ref(self, consumer_id, DataConsumer)

    def add(self, consumers: DataConsumer | list[DataConsumer]) -> DataConsumerScheme:
        """Return a new DataConsumerScheme with *consumers* added.

        Accepts a single DataConsumer or a list of them.
        """
        new = [consumers] if isinstance(consumers, DataConsumer) else list(consumers)
        return evolve(self, consumers=[*self.consumers, *new])

    def drop(self, ids: str | list[str]) -> DataConsumerScheme:
        """Return a new DataConsumerScheme with the given consumer(s) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        wanted = [ids] if isinstance(ids, str) else list(ids)
        missing = [i for i in wanted if i not in self.consumers]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(wanted)
        return evolve(self, consumers=[item for item in self.consumers if item.id not in id_set])

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


@frozen(unsafe_hash=False)
class OrganisationUnitScheme(MaintainableArtefact):
    """Maintainable container for OrganisationUnit items, hierarchical through `OrganisationUnit.parent_id` (#469)."""

    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "OrganisationUnitScheme"
    msg_field: ClassVar[str] = "organisation_unit_schemes"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    organisation_units: ItemList[OrganisationUnit] = field(factory=ItemList, converter=_to_item_list_unit)
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

    def ref_to(self, unit_id: str) -> Ref[OrganisationUnit]:
        """Build a resolved `Ref` to an organisation unit within this scheme.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist (#407).

        Raises:
            KeyError: If no organisation unit with this id is in this scheme.
        """
        return item_ref(self, unit_id, OrganisationUnit)

    def add(self, organisation_units: OrganisationUnit | list[OrganisationUnit]) -> OrganisationUnitScheme:
        """Return a new OrganisationUnitScheme with *organisation_units* added.

        Accepts a single OrganisationUnit or a list of them.
        """
        new = [organisation_units] if isinstance(organisation_units, OrganisationUnit) else list(organisation_units)
        return evolve(self, organisation_units=[*self.organisation_units, *new])

    def drop(self, ids: str | list[str]) -> OrganisationUnitScheme:
        """Return a new OrganisationUnitScheme with the given organisation unit(s) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        wanted = [ids] if isinstance(ids, str) else list(ids)
        missing = [i for i in wanted if i not in self.organisation_units]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(wanted)
        return evolve(self, organisation_units=[item for item in self.organisation_units if item.id not in id_set])

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)
