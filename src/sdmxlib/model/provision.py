"""ProvisionAgreement — binds a data provider to a dataflow."""

from datetime import datetime
from typing import ClassVar

from attrs import field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text, to_optional_ref
from sdmxlib.model.urn import SdmxUrn


def _to_dataflow_ref(v: Ref[Dataflow] | SdmxUrn[Dataflow] | Dataflow | None) -> Ref[Dataflow] | None:
    if isinstance(v, Dataflow):
        return Ref.to(v)
    return to_optional_ref(v)


@frozen(unsafe_hash=False)
class ProvisionAgreement(MaintainableArtefact):
    """Binds a data provider to a dataflow.

    Example:
        pa.dataflow()  # Dataflow (if resolved)
        pa.dataflow().structure()  # DataStructure
        pa.dataflow().structure.dimensions  # ItemList[Dimension]
    """

    sdmx_package: ClassVar[str] = "registry"
    sdmx_class: ClassVar[str] = "ProvisionAgreement"
    msg_field: ClassVar[str] = "provision_agreements"

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
    dataflow: Ref[Dataflow] | None = field(default=None, converter=_to_dataflow_ref, validator=refuse_urn_text)
    data_provider: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
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
