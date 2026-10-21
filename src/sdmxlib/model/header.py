"""A message's header: who sent it, to whom, when, and what about (#480).

Every SDMX-ML message opens with a ``<mes:Header>`` and every SDMX-JSON one
with a ``meta`` object. A structure message's header allows seven elements:
``ID``, ``Test``, ``Prepared``, ``Sender``, ``Receiver*``, ``Name*`` and
``Source*`` (``StructureHeaderType``). A reference-metadata message's adds a
provider, an action, data set ids and an extraction time
(``GenericMetadataHeaderType``). The parties are a ``PartyType``: an id, names
and contacts, and the sender a time zone besides.

The header ``Structure`` elements of a metadata message are not held here:
they name, for the wire, the structure each set's ``@structureRef`` points at,
which `MetadataSet.structure` already holds, so the writer derives them.

A message without a header holds `None`, and a writer then emits fixed
placeholders (``ID`` ``SDMX_MSG``, prepared 2000-01-01, sender ``sdmxlib``),
so the same message always serialises to the same bytes.
"""

import re
from datetime import datetime
from enum import Enum

import attrs
from attrs import field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._rules import _refuse, id_problem, id_type
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.istring import to_istring as _to_istring
from sdmxlib.model.organisation import Contact, MetadataProvider
from sdmxlib.model.ref import Ref, refuse_urn_text, to_optional_ref

#: ``TimezoneType``: ``Z``, or an offset from -14:00 to +14:00.
_TIMEZONE = re.compile(r"Z|[+-](14:00|(0[0-9]|1[0-3]):[0-5][0-9])")


class ActionType(Enum):
    """What a message's data or metadata sets do to what the receiver holds (``ActionType``)."""

    APPEND = "Append"
    REPLACE = "Replace"
    DELETE = "Delete"
    #: Sent for information only: nothing is to be done with it.
    INFORMATION = "Information"


def _ids(instance: object, attribute: attrs.Attribute[tuple[str, ...]], value: tuple[str, ...]) -> None:
    for each in value:
        _refuse(instance, attribute.name, id_problem(each))


def _timezone(instance: object, attribute: attrs.Attribute[str | None], value: str | None) -> None:
    if value is not None and not _TIMEZONE.fullmatch(value):
        _refuse(instance, attribute.name, f"{value!r} is not a time zone: Z, or an offset from -14:00 to +14:00")


@frozen
class Party:
    """A party to a message, a receiver or the sender (``PartyType``).

    Attributes:
        id: The party's id, an SDMX ``IDType``.
        name: Its names, by language.
        contacts: How to reach it. SDMX-ML's message contact has no id, which
            SDMX-JSON's has; SDMX-ML writes one without it.
    """

    id: str = field(validator=id_type)
    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    contacts: tuple[Contact, ...] = field(factory=tuple, converter=tuple)


@frozen
class Sender(Party):
    """The party that sent a message (``SenderType``): a `Party` and its time zone.

    Attributes:
        timezone: ``Z`` or an offset such as ``-05:00``; SDMX-JSON has no
            place for it.
    """

    timezone: str | None = field(default=None, validator=_timezone)


@frozen(kw_only=True)
class Header:
    """A structure message's header (``StructureHeaderType``).

    Attributes:
        id: The message's id, an SDMX ``IDType``.
        prepared: When the message was prepared, tagged UTC as every model
            datetime is. A document's ``xs:date`` reads as its midnight.
        sender: Who sent it.
        test: Whether it is a test message.
        receivers: Who it is for.
        name: The message's names, by language.
        source: Where its content comes from, by language; SDMX-JSON has no
            place for it.

    Example:
        header = Header(id="REQ1", prepared=datetime(2026, 10, 20, tzinfo=UTC), sender=Sender(id="ESTAT"))
        msg = StructureMessage(header=header, codelists=[...])
    """

    id: str = field(validator=id_type)
    prepared: datetime = field(converter=as_utc)
    sender: Sender
    test: bool = False
    receivers: tuple[Party, ...] = field(factory=tuple, converter=tuple)
    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    source: InternationalString = field(factory=InternationalString, converter=_to_istring)


@frozen(kw_only=True)
class MetadataHeader(Header):
    """A reference-metadata message's header (``GenericMetadataHeaderType``): a `Header` and four more.

    SDMX-JSON has a place for none of the four.

    Attributes:
        metadata_provider: The provider the metadata sets come from.
        data_set_action: What the sets do to what the receiver holds.
        data_set_ids: The sets' ids.
        extracted: When the metadata was extracted from its source, tagged UTC.
    """

    metadata_provider: Ref[MetadataProvider] | None = field(
        default=None, converter=to_optional_ref, validator=refuse_urn_text
    )
    data_set_action: ActionType | None = None
    data_set_ids: tuple[str, ...] = field(factory=tuple, converter=tuple, validator=_ids)
    extracted: datetime | None = field(default=None, converter=as_utc)
