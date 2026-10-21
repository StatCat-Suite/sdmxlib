"""Dataflow — a published data product backed by a DataStructure."""

from datetime import datetime
from typing import ClassVar

from attrs import field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text
from sdmxlib.model.urn import SdmxUrn


def _to_structure(
    v: Ref[DataStructure] | DataStructure | SdmxUrn[DataStructure] | None,
) -> Ref[DataStructure] | None:
    if isinstance(v, SdmxUrn):
        return Ref[DataStructure].unresolved(v)
    if isinstance(v, DataStructure):
        return Ref.to(v)
    return v


@frozen(unsafe_hash=False)
class Dataflow(MaintainableArtefact):
    """A published data product backed by a DataStructure.

    Example:
        df.structure  # Ref[DataStructure]
        df.structure()  # DataStructure (if resolved)
        df.structure.dimensions  # convenience via resolved structure
    """

    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "Dataflow"
    msg_field: ClassVar[str] = "dataflows"

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
    structure: Ref[DataStructure] | None = field(default=None, converter=_to_structure, validator=refuse_urn_text)
    #: Dimensions this Dataflow commits to using, by id.
    #:
    #: SDMX 3.1 ``DataflowType`` adds an optional ``<DimensionConstraint>``
    #: element holding one or more ``<Dimension>id</Dimension>`` children.
    #: It supports the *evolving DSD* pattern: a DataStructure can grow new
    #: dimensions in minor versions, and a Dataflow with a wildcarded
    #: minor-version pin to that DSD declares which dimensions it commits
    #: to so future DSD additions don't silently appear in datasets.
    #:
    #: Empty tuple = no constraint (the Dataflow accepts the full DSD
    #: dimensionality). The time dimension must not be listed per the XSD
    #: documentation — it's always implicitly included.
    #:
    #: 2.1 and 3.0 have no equivalent element; serializers for those
    #: versions ignore the field.
    dimension_constraint: tuple[str, ...] = field(factory=tuple, converter=tuple)
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
