"""Metadataflow — a published reference-metadata product backed by an MSD."""

from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

from attrs import evolve, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._items import _maintainable_classes, owner_classes, spellings
from sdmxlib.model._referenceable import PAIRS_30, PAIRS_31, PAIRS_JSON
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text
from sdmxlib.model.urn import SdmxUrn


def _to_msd_ref(
    v: Ref[MetadataStructure] | MetadataStructure | SdmxUrn[MetadataStructure] | None,
) -> Ref[MetadataStructure] | None:
    if isinstance(v, SdmxUrn):
        return Ref[MetadataStructure].unresolved(v)
    if isinstance(v, MetadataStructure):
        return Ref.to(v)
    return v


@frozen(unsafe_hash=False)
class Metadataflow(MaintainableArtefact):
    """A published reference-metadata product backed by a MetadataStructure.

    Mirrors Dataflow: binds a maintained id/version to one MSD so that
    metadata sets can reference the flow rather than the bare structure.
    Per the SDMX 3.0 XSD, a Metadataflow also carries one or more
    ``targets`` — URN references to the identifiable artefacts the
    reported metadata applies to. URNs may include wildcards per
    ``WildcardUrnType`` so we model them as raw strings rather than
    resolved Refs.

    Example:
        mdf = sl.Metadataflow(
            id="MDF_FOOTNOTES", maintainer="STATCAN",
            structure=msd,
            targets=("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ESTAT:CPI(*)",),
            name={"en": "Footnotes flow"},
        )
    """

    sdmx_package: ClassVar[str] = "metadatastructure"
    sdmx_class: ClassVar[str] = "Metadataflow"
    msg_field: ClassVar[str] = "metadataflows"

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
    structure: Ref[MetadataStructure] | None = field(default=None, converter=_to_msd_ref, validator=refuse_urn_text)
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

    def target_misfits(self, available: Iterable[MetadataStructure] = ()) -> tuple[str, ...]:
        """Why `with_derived_targets` cannot give this flow targets, or nothing when it can (#421).

        Args:
            available: Metadata structures to find this flow's in when its
                reference is not resolved — a message's own, say.

        Returns:
            One reason per obstacle; empty when the targets can be derived.
        """
        return self._derived(available)[1]

    def with_derived_targets(self, available: Iterable[MetadataStructure] = ()) -> Metadataflow:
        """This flow with the targets its 2.1 metadata structure implies (#421).

        SDMX 3.x requires a metadataflow to name at least one target — what
        the metadata reported against it may describe. SDMX-ML 2.1 put that
        in the metadata structure instead, as `MetadataTarget` s, so a flow
        read from 2.1 has none. Each ``IdentifiableObjectTarget`` there names a
        class of object and becomes the wildcard URN naming every object of
        that class: ``Dataflow`` gives
        ``urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=*:*(*)``. The
        other target kinds — a data key, a data set, a reporting period, a
        constraint — describe something within data, which no URN names, so
        they contribute nothing.

        Args:
            available: Metadata structures to find this flow's in when its
                reference is not resolved — a message's own, say.

        Returns:
            A new flow, or this one when it already has targets.

        Raises:
            ValueError: If the targets cannot be derived; `target_misfits`
                says why in advance.
        """
        if self.targets:
            return self
        targets, misfits = self._derived(available)
        if misfits:
            msg = f"cannot derive targets for {self.urn}: {'; '.join(misfits)}"
            raise ValueError(msg)
        return evolve(self, targets=targets)

    def _derived(self, available: Iterable[MetadataStructure]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The wildcard targets this flow's metadata structure implies, and why any cannot be."""
        if self.structure is None:
            return (), ("it names no metadata structure",)
        msd = self.structure() if self.structure.resolved else None
        if msd is None:
            urn = str(self.structure.urn)
            msd = next((candidate for candidate in available if str(candidate.urn) == urn), None)
        if msd is None:
            return (), (f"its metadata structure {self.structure.urn} is neither resolved nor in the message",)
        types = dict.fromkeys(
            obj.object_type
            for target in msd.targets
            for obj in target.target_objects
            if obj.kind == "IdentifiableObjectTarget"
        )
        if not types:
            return (), (f"{msd.urn} declares no IdentifiableObjectTarget",)
        wildcards = {object_type: _wildcard(object_type) for object_type in types}
        return (
            tuple(w for w in wildcards.values() if w is not None),
            tuple(
                f"{msd.urn} targets objects of type {object_type!r}, which no 3.x wildcard URN names"
                for object_type, w in wildcards.items()
                if w is None
            ),
        )


#: The classes a URN may name in every 3.x target: what a derived target can be.
_CLASSES_3X: dict[str, str] = {cls: pkg for pkg, cls in PAIRS_30 & PAIRS_31 & PAIRS_JSON}


#: 2.1 object types no derived target can stand for: 3.x's ``base.Any`` is a
#: reference class, and a target of anything says nothing a reader can check.
_UNTARGETABLE = frozenset({"Any"})


def _wildcard(object_type: str | None) -> str | None:
    """The 3.x wildcard URN naming every object of a 2.1 `object_type`, or None if none can.

    A 2.1 spelling is folded to its 3.x one (``Attribute`` to ``DataAttribute``).
    An item class wildcards its scheme and its own id, ``Code=*:*(*).*``. See
    `_UNTARGETABLE` for the type that has no such URN.
    """
    if object_type is None or object_type in _UNTARGETABLE:
        return None
    for spelling in spellings(object_type):
        package = _CLASSES_3X.get(spelling)
        if package is None:
            continue
        maintainable = any(getattr(cls, "sdmx_class", None) == spelling for cls in _maintainable_classes())
        if maintainable:
            return f"urn:sdmx:org.sdmx.infomodel.{package}.{spelling}=*:*(*)"
        if owner_classes(spelling):
            return f"urn:sdmx:org.sdmx.infomodel.{package}.{spelling}=*:*(*).*"
    return None
