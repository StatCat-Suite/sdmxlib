"""ValueList and ValueItem — SDMX 3.x's enumeration that is not a codelist."""

from datetime import datetime
from typing import ClassVar

from attrs import field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ItemList, to_itemlist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring


@frozen(unsafe_hash=False)
class ValueItem:
    """One value of a `ValueList`: the value, optionally named and described.

    Not identifiable, as a `Code` is: SDMX gives a value item no URN and no
    ``@uri``, and its ``id`` is any string rather than an SDMX identifier — it
    *is* the value, which may hold characters an id may not.
    """

    id: str
    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)


@frozen(unsafe_hash=False)
class ValueList(MaintainableArtefact):
    """A closed set of values a component may take (SDMX 3.x).

    What a component's representation enumerates when the values need no
    hierarchy and no identity of their own — a `Codelist` without either. Its
    URN class is ``ValueList``, so a component enumerated by one holds a
    ``Ref[ValueList]``; `RepresentationMixin.resolved_enumeration` answers for
    either kind. SDMX-ML 2.1 has no such class, so a 2.1 write leaves it out,
    with everything that references it (`sdmxlib.dropped`).

    Example:
        ValueList(
            id="VL_UNIT", maintainer="ESTAT", name={"en": "Units"},
            items=[ValueItem(id="kg", name={"en": "Kilogram"})],
        )
    """

    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "ValueList"
    msg_field: ClassVar[str] = "value_lists"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    items: ItemList[ValueItem] = field(factory=ItemList, converter=to_itemlist)
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

    def code_ids(self) -> list[str]:
        """Every value, in order — `EnumerationLike.code_ids`."""
        return self.items.ids()

    def code_map(self, *, lang: str) -> dict[str, str]:
        """``{value: label}`` in ``lang``, falling back to the value — `EnumerationLike.code_map`."""
        return {item.id: item.name.get(lang) or item.id for item in self.items}

    def __len__(self) -> int:
        """How many values this list holds."""
        return len(self.items)
