"""What an SDMX-ML read could not hold, declared and detected (#422).

The read-side twin of `formats/gaps.py`. The readers fetch what the model has
a field for and nothing else, so SDMX 3.x content with no field vanished
without a sound — the failure `gaps.py` exists to make answerable on the write
side. Two halves, one per shape the loss takes:

* **Content inside an artefact it did build** — a 2.1 structure set's
  `HybridCodelistMap`. The artefact is kept and the loss recorded as a
  `ReadGap` on `StructureMessage.lost`: refusing a StructureSet for one map
  would cost everything else it says. Declared in `LOSSES`, one row per
  construct, each with the XPath that counts it; a row leaves the table when
  the model learns to hold it, and the round-trip guard in
  `tests/unit/test_read_losses.py` — which re-counts with the same XPath
  after a write — fails until it does.
* **A whole artefact it built nothing from** — a `Process`, a `MetadataConstraint`.
  Every child of every container under `mes:Structures` must come back as an
  artefact or a refusal; one that did neither is refused here. Not a list of
  unmodelled containers: the account is taken of the *document*, so a class
  the readers skip is caught whether or not anyone thought to list it, and so
  is a reader that drops one it does model.

#436's closed-world check cannot do either: it refuses elements the schema
forbids, and everything here is content the schema allows.
"""

import functools
from collections.abc import Callable

from attrs import evolve, frozen
from lxml import etree

from sdmxlib.formats._refuse import xml_identity
from sdmxlib.formats.sdmx_ml._allowed_children import NAMESPACES
from sdmxlib.formats.sdmx_ml._closed_world import BY_STRUCTURE_NS, SdmxMlVersion, frame_refusals
from sdmxlib.formats.sdmx_ml21._synthetic import SYNTHETIC_STRUCTURE_SET_TYPE
from sdmxlib.model._items import _maintainable_classes
from sdmxlib.model._referenceable import PAIRS_30
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.errors import ReadAssumption, ReadGap, Refusal
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import artefact_urn
from sdmxlib.model.urn import SdmxUrn

#: An element's local name → the model class a reader builds from it, where
#: the two differ. 2.1 calls a data constraint `ContentConstraint`.
_BUILT_AS = {"ContentConstraint": "DataConstraint"}


@frozen
class Loss:
    """One construct a reader cannot hold, and how to count it in an artefact.

    Attributes:
        versions: The SDMX-ML versions whose reader loses it.
        construct: The construct, as the format spells it.
        reason: What the model holds instead.
        xpath: Counts occurrences under an artefact element; the prefixes are
            the version's (`NAMESPACES`).
    """

    versions: tuple[SdmxMlVersion, ...]
    construct: str
    reason: str
    xpath: str


_V3: tuple[SdmxMlVersion, ...] = ("3.0", "3.1")

#: The ids of the levels a node's own hierarchy defines, from the node's
#: `<Level>` reference. A defined level carries `@id` and a reference does
#: not, in both generations.
_DEFINED_LEVELS = "ancestor::str:Hierarchy[1]/descendant::str:Level[@id]/@id"

#: Every construct an SDMX-ML reader is known to drop. A row is deleted when
#: the model learns to hold its construct.
#:
#: #422 burned the table down to these, which stay as declared losses — the
#: structure set kept, the map reported: a ``ReportingTaxonomyMap`` while
#: ``ReportingTaxonomy`` is unmodelled, and a ``HybridCodelistMap``, which is
#: 2.1's alone. An ``OrganisationSchemeMap`` was here until #448 modelled it.
#:
#: Each XPath runs with the *artefact's* element as its context, so a
#: construct inside the artefact element itself is reached through
#: ``descendant-or-self::`` — ``.//str:StructureMap`` from a StructureMap
#: finds nothing.
LOSSES: tuple[Loss, ...] = (
    # Found by the 2.1 element census.
    *(
        Loss(
            ("2.1",),
            f"str:StructureSet/str:{child}",
            f"the model has no {child}; a structure set holds the maps it has a class for",
            f"count(descendant-or-self::str:StructureSet/str:{child})",
        )
        for child in ("HybridCodelistMap", "ReportingTaxonomyMap")
    ),
    # Found by the attribute census (`tests/unit/test_attribute_census.py`):
    # attributes, which the element census cannot see. A flag is counted only
    # where it says something its default does not.
    #
    # A node naming a level its hierarchy does not define (#501). This is not
    # a construct the model could learn to hold: the reference is to nothing,
    # so this row stays. The node is read with no level and the tree is kept.
    # 2.1 wraps the id in a `<Ref>`, while 3.x writes it as text; both readers
    # take either, so the XPath checks both spellings.
    Loss(
        ("2.1", *_V3),
        "str:HierarchicalCode/str:Level",
        "the hierarchy defines no level of that id, so the node is read with no level",
        "count(descendant-or-self::str:HierarchicalCode/str:Level[Ref/@id or normalize-space(.)]"
        f"[not(Ref/@id = {_DEFINED_LEVELS}) and not(normalize-space(.) = {_DEFINED_LEVELS})])",
    ),
)


@frozen
class Assumption:
    """One required construct a reader fills in when a document omits it (#474).

    `LOSSES`' counterpart for what a reader supplies rather than drops: the
    reader keeps its tolerance, and the read records it on
    `StructureMessage.assumed`, so an assumed value never passes for a stated
    one.

    Attributes:
        versions: The SDMX-ML versions whose schema requires the construct.
        construct: The missing construct, as the format spells it.
        assumed: The value the reader uses in its place.
        reason: Why that value.
        xpath: Counts the occurrences missing it under an artefact element:
            an XPath, or a function where XPath 1.0 cannot say it (a
            case-folded match against the artefact's own ids).
    """

    versions: tuple[SdmxMlVersion, ...]
    construct: str
    assumed: str
    reason: str
    xpath: str | Callable[[etree._Element], float]


def _dimensions_matched_by_case(dsd: etree._Element) -> float:
    """How many of a DSD's attribute relationships name a dimension in another case (#454).

    Counted on the document, beside `_attachment.match_dimension_case`, which
    the readers run on the model: a reference that is no dimension's id but
    names exactly one ignoring case. 3.x spells the reference as text, 2.1 as
    a ``Ref`` with an ``id``.
    """
    components = dsd.find("{*}DataStructureComponents")
    if components is None:
        return 0.0
    ids = {d.get("id") for d in components.iterfind("{*}DimensionList/*") if d.get("id") is not None}
    folded: dict[str, int] = {}
    for dimension_id in ids:
        if dimension_id is not None:
            folded[dimension_id.casefold()] = folded.get(dimension_id.casefold(), 0) + 1
    n = 0
    for reference in components.iterfind("{*}AttributeList/*/{*}AttributeRelationship/{*}Dimension"):
        ref = reference.find("{*}Ref")
        if ref is None:
            ref = reference.find("Ref")
        name = ref.get("id", "") if ref is not None else (reference.text or "").strip()
        if name not in ids and folded.get(name.casefold()) == 1:
            n += 1
    return float(n)


#: Where a DSD's attributes are, as a path from the artefact: never a
#: descendant search, which walked every codelist's codes for nothing.
_ATTRIBUTE_LIST = "self::str:DataStructure/str:DataStructureComponents/str:AttributeList"

_RELATIONSHIP_REASON = (
    "read as attached to the dataset, the tolerance chosen in #421: FMR and IMF omit the "
    "relationship on thousands of components, and refusing them blocked 166 of 186 IMF DSDs"
)

_UNVERSIONED_REASON = (
    "3.x reads an artefact without a version as un-versioned, which the model cannot hold; "
    "1.0 is the version 2.1's schema gives the same omission (#478)"
)

#: 3.x artefacts whose schema prohibits ``@version``: versionless by the
#: standard, not by omission, so read as 1.0 with nothing assumed. 3.1 adds
#: `Categorisation`.
_VERSIONLESS_30 = (
    "AgencyScheme",
    "DataConsumerScheme",
    "DataProviderScheme",
    "MetadataProviderScheme",
    "OrganisationUnitScheme",
)

#: Every required construct a reader fills in when a document omits it. A row
#: leaves when its reader refuses instead, or when the schema stops requiring
#: it. Each XPath runs with the artefact's element as context, as in `LOSSES`.
ASSUMPTIONS: tuple[Assumption, ...] = (
    *(
        Assumption(
            ("2.1",),
            f"str:{component}/str:AttributeRelationship",
            "attachment DATASET",
            _RELATIONSHIP_REASON,
            f"count({_ATTRIBUTE_LIST}/str:{component}[not(str:AttributeRelationship)])",
        )
        for component in ("Attribute", "ReportingYearStartDay")
    ),
    *(
        Assumption(
            _V3,
            f"str:{component}/str:AttributeRelationship",
            "attachment DATASET",
            _RELATIONSHIP_REASON,
            f"count({_ATTRIBUTE_LIST}/str:{component}[not(str:AttributeRelationship)])",
        )
        for component in ("Attribute", "MetadataAttributeUsage")
    ),
    *(
        Assumption(
            (version,),
            "@version",
            "1.0",
            _UNVERSIONED_REASON,
            f"count(self::*[not(@version)][not({' or '.join(f'self::str:{name}' for name in versionless)})])",
        )
        for version, versionless in (("3.0", _VERSIONLESS_30), ("3.1", (*_VERSIONLESS_30, "Categorisation")))
    ),
    Assumption(
        ("3.0",),
        "str:DataConstraint/@role",
        "Allowed",
        "3.0 requires the role and gives it no default; Allowed is the reading 3.1 makes of a data "
        "constraint, which has no role at all",
        "count(self::str:DataConstraint[not(@role)])",
    ),
    Assumption(
        ("2.1", *_V3),
        "str:AttributeRelationship/str:Dimension",
        "the dimension of that id in another case",
        "SDMX ids are case-sensitive, but a reference naming exactly one dimension ignoring case can mean only "
        "that one: Eurostat's 3.0 DSDs name `freq` as `FREQ` (#454)",
        _dimensions_matched_by_case,
    ),
    Assumption(
        _V3,
        "str:EpochMap/@epochPeriod",
        "day",
        "the period an epoch counts in has no default in the schema; a day is the finest it allows",
        "count(self::str:StructureMap/str:EpochMap[not(@epochPeriod)])",
    ),
    Assumption(
        ("2.1", *_V3),
        "str:TimeDimension/str:LocalRepresentation",
        "ObservationalTimePeriod",
        "the schema requires a time dimension's format and defaults its type to ObservationalTimePeriod (#507)",
        "count(self::str:DataStructure/str:DataStructureComponents/str:DimensionList"
        "/str:TimeDimension[not(str:LocalRepresentation)])",
    ),
    Assumption(
        _V3,
        "str:Hierarchy/@hasFormalLevels",
        "false",
        "a hierarchy that does not say it has formal levels is read as having none",
        "count(self::str:Hierarchy[not(@hasFormalLevels)])",
    ),
)


#: Containers whose children have no identity to account by. 3.1's
#: availability constraints have no id, agency or version; the reader builds or
#: refuses each one itself, by position (#421).
_NOT_MAINTAINABLE = frozenset({"AvailabilityConstraints"})


def account(structures: etree._Element, msg: StructureMessage) -> StructureMessage:
    """`msg` with what the read could not hold: refusals for what it built nothing from, and losses.

    Args:
        structures: The document's `mes:Structures` element.
        msg: What the reader built from it.

    Returns:
        `msg`, the same object when the document was held whole.
    """
    built = {str(artefact_urn(artefact)) for artefact in msg.all_artefacts()}
    accounted = built | {refusal.urn for refusal in msg.refused}
    # The frame first: what it refuses is refused by its place, and not
    # accounted for again below as an artefact nobody built (#447).
    refused, covered = frame_refusals(structures)
    lost: list[ReadGap] = []
    assumed: list[ReadAssumption] = []
    # Per call, never cached at import: a partial import would freeze a
    # partial answer, the failure `_items._maintainable_classes` documents (#405).
    classes = {cls.__name__: cls for cls in _maintainable_classes() if issubclass(cls, MaintainableArtefact)}
    for container in structures:
        if not isinstance(container.tag, str) or container in covered:
            continue  # a comment or processing instruction, or refused by the frame
        # Each container's namespace names its version, as `check_artefact`
        # reads an artefact's, so no reader threads it here.
        version = BY_STRUCTURE_NS.get(etree.QName(container).namespace or "")
        if version is None or etree.QName(container).localname in _NOT_MAINTAINABLE:
            continue
        counters = _counters(version)
        assumption_counters = _assumption_counters(version)
        for el in container:
            if not isinstance(el.tag, str) or el in covered:
                continue  # a comment or processing instruction, or refused by the frame
            name = etree.QName(el).localname
            urn, modelled = _urn(name, el, classes)
            if _invented(el) or (urn not in built and _folded(name, urn, accounted)):
                continue
            if urn in built:
                lost.extend(_losses(urn, el, counters))
                assumed.extend(_assumptions(urn, el, assumption_counters))
            elif urn not in accounted:
                reason = (
                    "the reader built nothing from it and refused nothing; this is a reader defect"
                    if modelled
                    else f"sdmxlib does not model {name}"
                )
                refused.append(Refusal(artefact_class=name, urn=urn, reason=reason))
    if not refused and not lost and not assumed:
        return msg
    return evolve(msg, refused=(*msg.refused, *refused), lost=(*msg.lost, *lost), assumed=(*msg.assumed, *assumed))


def _invented(el: etree._Element) -> bool:
    """A StructureSet sdmxlib invented on a 2.1 write: its maps are read, it is not (#242)."""
    return any(
        (t.text or "").strip() == SYNTHETIC_STRUCTURE_SET_TYPE
        for t in el.iterfind("{*}Annotations/{*}Annotation/{*}AnnotationType")
    )


#: 2.1 elements a reader builds as another class, by the URN substitution
#: that names what it built: the class, and for a provider scheme its fixed id.
#:
#: - a HierarchicalCodelist read as the one hierarchy it only wraps, which
#:   has its agency, id and version;
#: - a ProvisionAgreement over a metadataflow, read as a metadata provision
#:   agreement (#421);
#: - a DataProviderScheme of metadata providers, read as the metadata
#:   provider scheme sdmxlib wrote it from (#421).
_FOLDED = {
    "HierarchicalCodelist": ((".HierarchicalCodelist=", ".Hierarchy="),),
    "ProvisionAgreement": ((".ProvisionAgreement=", ".MetadataProvisionAgreement="),),
    "DataProviderScheme": (
        (".DataProviderScheme=", ".MetadataProviderScheme="),
        (":DATA_PROVIDERS(", ":METADATA_PROVIDERS("),
    ),
}


def _folded(name: str, urn: str, accounted: set[str]) -> bool:
    """An element the reader built, or refused, as another class (`_FOLDED`), and nothing of its own."""
    substitutions = _FOLDED.get(name)
    if substitutions is None:
        return False
    for old, new in substitutions:
        urn = urn.replace(old, new, 1)
    return urn in accounted


@functools.cache
def _counters(version: SdmxMlVersion) -> list[tuple[Loss, etree.XPath]]:
    """`LOSSES` for one version, compiled once."""
    namespaces = NAMESPACES[version]
    return [(loss, etree.XPath(loss.xpath, namespaces=namespaces)) for loss in LOSSES if version in loss.versions]


def _losses(urn: str, el: etree._Element, counters: list[tuple[Loss, etree.XPath]]) -> list[ReadGap]:
    gaps: list[ReadGap] = []
    for loss, count in counters:
        n = count(el)
        if isinstance(n, float) and n:
            gaps.append(ReadGap(urn=urn, construct=loss.construct, count=int(n), reason=loss.reason))
    return gaps


@functools.cache
def _assumption_counters(version: SdmxMlVersion) -> list[tuple[Assumption, Callable[[etree._Element], object]]]:
    """`ASSUMPTIONS` for one version, compiled once."""
    namespaces = NAMESPACES[version]
    return [
        (a, etree.XPath(a.xpath, namespaces=namespaces) if isinstance(a.xpath, str) else a.xpath)
        for a in ASSUMPTIONS
        if version in a.versions
    ]


def _assumptions(
    urn: str, el: etree._Element, counters: list[tuple[Assumption, Callable[[etree._Element], object]]]
) -> list[ReadAssumption]:
    found: list[ReadAssumption] = []
    for assumption, count in counters:
        n = count(el)
        if isinstance(n, float) and n:
            found.append(
                ReadAssumption(
                    urn=urn,
                    construct=assumption.construct,
                    assumed=assumption.assumed,
                    count=int(n),
                    reason=assumption.reason,
                )
            )
    return found


def _urn(name: str, el: etree._Element, classes: dict[str, type[MaintainableArtefact]]) -> tuple[str, bool]:
    """The URN an element names, and whether the model has its class.

    Built from the element's own agency, id and version — as `_refuse` names a
    refusal, defaulted as the readers default them — so it equals the URN of
    what a reader built from it.
    """
    agency, id_, version = xml_identity(el)
    cls = classes.get(_BUILT_AS.get(name, name))
    if cls is not None:
        package: str = cls.sdmx_package
        artefact_class: str = cls.sdmx_class
        modelled = True
    else:
        package = next((p for p, c in PAIRS_30 if c == name), "unknown")
        artefact_class, modelled = name, False
    urn = SdmxUrn[object](
        package=package, artefact_class=artefact_class, agency=agency, id=id_, version=version or "latest"
    )
    return str(urn), modelled
