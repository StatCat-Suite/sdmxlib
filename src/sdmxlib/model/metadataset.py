"""MetadataSet and ReportedAttribute — populated reference metadata.

A MetadataSet binds an MSD (or Metadataflow) to a list of target artefacts
and a tree of populated attribute values. All attributes apply to all
targets in the set — this is the canonical SDMX 3.0 shape (the v2.1
``MetadataReport`` layer was dropped in 3.0).

Repetition (when the corresponding MetadataAttribute has ``max_occurs > 1``)
is expressed by multiple sibling ReportedAttributes with the same id, not
by a values list on the leaf — again matching the wire formats.

Values are scalars (str, int, float, bool) or InternationalString for
multilingual / XHTML content. The MSD declares whether a string scalar is
a code id or free text; the wire format does not distinguish them, so
neither does the model.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar, cast

from attrs import evolve, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ItemList, to_itemlist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text
from sdmxlib.model.urn import SdmxUrn

# A populated reported value. Scalar types map to JSON primitives and to
# the text content of XML <Value>; InternationalString maps to a JSON
# localisedText dict and to one or more <com:Text> / <com:StructuredText>
# (XHTML) sibling elements.
type ReportedAttributeValue = str | int | float | bool | InternationalString


def _to_value(
    v: ReportedAttributeValue | dict[str, str] | None,
) -> ReportedAttributeValue | None:
    """Coerce a dict to InternationalString; pass scalars through."""
    if isinstance(v, dict):
        return InternationalString.of(v)
    return v


@frozen(unsafe_hash=False)
class ReportedAttribute:
    """One populated metadata attribute within a MetadataSet.

    The id field references a MetadataAttribute in the MSD. Either the
    value is set (leaf) or children is non-empty (presentational
    container). Repetition is handled by multiple sibling instances with
    the same id.

    Example:
        sl.ReportedAttribute(id="FOOTNOTE_TEXT", value={"en": "..."})
        sl.ReportedAttribute(id="FREQ", value="A")
        sl.ReportedAttribute(id="QUALITY", children=[
            sl.ReportedAttribute(id="COVERAGE", value="full"),
        ])
    """

    id: str
    value: ReportedAttributeValue | None = field(default=None, converter=_to_value)
    children: ItemList[ReportedAttribute] = field(factory=ItemList, converter=to_itemlist)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None


@frozen
class MetadataSection:
    """A flattened, MSD-typed view of one reported metadata value.

    The retrieval projection of a MetadataSet: one ``MetadataSection`` per
    ``metadata_value`` row, with the leaf attribute's declared data type
    (``text_type``, e.g. ``"String"`` / ``"XHTML"``) joined in from the
    MSD. This is the sectioned input a chunker / search indexer wants —
    ``path`` gives the dotted hierarchical location (with ``[n]`` sibling
    suffixes preserved), ``attribute_id`` the leaf id, and ``value`` the
    scalar or ``InternationalString`` content.

    Produced by ``Registry.metadata_report``.
    """

    path: str
    attribute_id: str
    sibling_index: int
    value: ReportedAttributeValue | None = None
    text_type: str | None = None
    is_presentational: bool = False


# ── MetadataSet ──────────────────────────────────────────────────────────


# A MetadataSet is reported against a Metadataflow or a MetadataProvisionAgreement
# — the two structure-ref options the SDMX 3.0 ``MetadataSetType`` xs:choice
# allows. There is *no* direct-MSD reference, so passing a MetadataStructure is
# rejected (wrap it in a Metadataflow instead).


def _to_structure_ref(
    v: Ref[Metadataflow]
    | Ref[MetadataProvisionAgreement]
    | SdmxUrn[Metadataflow]
    | SdmxUrn[MetadataProvisionAgreement]
    | Metadataflow
    | MetadataProvisionAgreement,
) -> Ref[Metadataflow] | Ref[MetadataProvisionAgreement]:
    if isinstance(v, MetadataStructure):
        msg = (
            "MetadataSet.structure cannot reference a MetadataStructure directly — "
            "the SDMX 3.0 model reports metadata against a Metadataflow (or a "
            "MetadataProvisionAgreement). Wrap the MSD in a Metadataflow: "
            "Metadataflow(id=..., maintainer=..., structure=msd)."
        )
        raise TypeError(msg)
    if isinstance(v, SdmxUrn):
        if v.artefact_class == "MetadataProvisionAgreement":
            return Ref[MetadataProvisionAgreement].unresolved(cast("SdmxUrn[MetadataProvisionAgreement]", v))
        return Ref[Metadataflow].unresolved(cast("SdmxUrn[Metadataflow]", v))
    # Split the two arms rather than `Ref.to(v)` on the union: `Ref` is
    # invariant in T, so a `Ref[Metadataflow | MetadataProvisionAgreement]`
    # is not a `Ref[Metadataflow] | Ref[MetadataProvisionAgreement]`.
    if isinstance(v, MetadataProvisionAgreement):
        return Ref[MetadataProvisionAgreement].to(v)
    if isinstance(v, Metadataflow):
        return Ref[Metadataflow].to(v)
    return v


def _to_target_refs(
    v: Sequence[Ref[object] | SdmxUrn[object]],
) -> list[Ref[object]]:
    """Coerce a sequence of URNs / Refs to a list of Refs."""
    out: list[Ref[object]] = []
    for item in v:
        if isinstance(item, SdmxUrn):
            out.append(Ref[object].unresolved(item))
        else:
            out.append(item)
    return out


@frozen(unsafe_hash=False)
class MetadataSet(MaintainableArtefact):
    """A populated reference-metadata payload describing a list of targets.

    All attributes apply to all targets — there is no per-target scoping.
    This matches the SDMX 3.0 information model, which collapsed v2.1's
    MetadataReport layer.

    The ``structure`` field references a Metadataflow or a
    MetadataProvisionAgreement — the two structure-ref options the SDMX 3.0
    model permits. (There is no direct-MSD reference; wrap an MSD in a
    Metadataflow.)

    Note that MetadataSet does NOT belong in a Structure message envelope —
    it is carried in a GenericMetadata message (``<mes:GenericMetadata>`` in
    SDMX-ML 3.0; ``data.metadataSets`` keyed on the metadata-schema $schema
    in SDMX-JSON 2.0). Use a ``MetadataMessage`` to serialise it.

    Example:
        ms = sl.MetadataSet(
            id="FOOTNOTE_A", maintainer="STATCAN",
            structure=mdf,
            targets=[char_1_urn, char_5_urn, char_12_urn],
            attributes=[sl.ReportedAttribute(id="FOOTNOTE_TEXT",
                                             value={"en": "..."})],
        )
    """

    sdmx_package: ClassVar[str] = "metadatastructure"
    sdmx_class: ClassVar[str] = "MetadataSet"
    msg_field: ClassVar[str] = "metadata_sets"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    structure: Ref[Metadataflow] | Ref[MetadataProvisionAgreement] = field(
        converter=_to_structure_ref, validator=refuse_urn_text
    )
    #: Semver, unlike every other maintainable's ``"1.0"``. SDMX-JSON's
    #: *metadata* schema pins ``VersionType`` to strict ``x.y.z`` while its
    #: *structure* schema states no pattern at all, so ``"1.0"`` had no valid
    #: SDMX-JSON form. `version` is optional in that schema — the SDMX-TWG's
    #: own sample omits it — so the reader defaulting to ``"1.0"`` and the
    #: writer then emitting it turned a valid input into an invalid output
    #: (#234).
    version: str = field(default="1.0.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    targets: list[Ref[object]] = field(factory=list, converter=_to_target_refs, validator=refuse_urn_text)
    attributes: ItemList[ReportedAttribute] = field(factory=ItemList, converter=to_itemlist)
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

    def add_target(self, target: Ref[object] | SdmxUrn[object]) -> MetadataSet:
        """Return a new MetadataSet with *target* appended to targets."""
        new_target = Ref[object].unresolved(target) if isinstance(target, SdmxUrn) else target
        return evolve(self, targets=[*self.targets, new_target])

    def add_attribute(self, attribute: ReportedAttribute) -> MetadataSet:
        """Return a new MetadataSet with *attribute* appended to attributes."""
        return evolve(self, attributes=[*self.attributes, attribute])
