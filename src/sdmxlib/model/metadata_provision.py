"""MetadataProvisionAgreement — binds a metadata provider to a metadataflow.

The reference-metadata analogue of `ProvisionAgreement` (which binds a *data*
provider to a *dataflow*). Per the SDMX 3.0 ``MetadataProvisionAgreementType``
it references a `Metadataflow` and a metadata provider, plus optional targets.

A `MetadataSet` may be reported against either a `Metadataflow` or a
`MetadataProvisionAgreement` — those are the only two structure-ref options the
SDMX 3.0 information model permits (there is no direct-MSD reference).
"""

from datetime import datetime
from typing import ClassVar

from attrs import field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.organisation import (
    Agency,
    MetadataProvider,
    _to_agency,
    _to_annotations,
    _to_istring,
)
from sdmxlib.model.ref import Ref, refuse_urn_text, to_optional_ref
from sdmxlib.model.urn import SdmxUrn


def _to_metadataflow_ref(
    v: Ref[Metadataflow] | SdmxUrn[Metadataflow] | Metadataflow | None,
) -> Ref[Metadataflow] | None:
    if isinstance(v, Metadataflow):
        return Ref.to(v)
    return to_optional_ref(v)


@frozen(unsafe_hash=False)
class MetadataProvisionAgreement(MaintainableArtefact):
    """Binds a metadata provider to a metadataflow (SDMX 3.0).

    Example:
        mpa.metadataflow()  # Metadataflow (if resolved)
        mpa.metadataflow().structure()  # MetadataStructure
    """

    sdmx_package: ClassVar[str] = "registry"
    sdmx_class: ClassVar[str] = "MetadataProvisionAgreement"
    msg_field: ClassVar[str] = "metadata_provision_agreements"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    metadataflow: Ref[Metadataflow] | None = field(
        default=None, converter=_to_metadataflow_ref, validator=refuse_urn_text
    )
    #: The metadata provider.
    #:
    #: Was ``Ref[object]``, "mirroring ``ProvisionAgreement.data_provider``" —
    #: but that mirror was false: the data twin resolves into
    #: `sdmx.data_provider`, while this pointed at a relation that did not
    #: exist. The stored ``metadata_provider_urn`` column was `TEXT NOT NULL`
    #: and held a urn nothing could resolve (#315). Narrowed now that
    #: `MetadataProvider` is modelled, so the type says what the ref reaches.
    metadata_provider: Ref[MetadataProvider] | None = field(
        converter=to_optional_ref, default=None, validator=refuse_urn_text
    )
    #: Optional structures the reported metadata is restricted to.
    #:
    #: `tuple[str, ...]`, not `Ref`, for the same reason `Metadataflow.targets`
    #: is: the XSD types these `WildcardUrnType`, so a target may name a whole
    #: class of artefacts (`...Dataflow=ESTAT:CPI(*)`) and no resolvable
    #: reference can express that. They were `list[Ref[object]]` while nothing
    #: read or wrote them — #362.
    targets: tuple[str, ...] = field(factory=tuple, converter=tuple)
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
