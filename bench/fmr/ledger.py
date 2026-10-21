"""What the FMR oracle removes from a document before FMR will take it, and why (#457).

The oracle sends FMR the maximal fixture and the version shapes, which set
every field rather than meaningful combinations of them, and FMR refuses some
of what they carry. Each refusal is answered here by one `Adjustment`: an XPath
naming what is removed, a `Kind` saying whose rule it is, and the reason, with
the XSD or IM text that decides it.

The ledger is checked both ways by the replay test: an adjustment must match
something in the document, and the recording must show FMR refusing the
document without it — so an adjustment FMR stops needing is reported, as is a
refusal no adjustment answers. Where FMR refused something of *ours* (a
mapping value that is no code of its codelist), the fixture was fixed instead;
`tests/support/shapes.py` says so where it happened.
"""

from typing import Literal

from attrs import frozen

type Version = Literal["2.1", "3.0"]
type Document = Literal["maximal", "shape"]
type Kind = Literal["fmr-bug", "fmr-deviation", "fmr-gap", "fmr-rule", "registry-rule"]

#: What each kind of adjustment says about whose rule it is.
KINDS: dict[Kind, str] = {
    "fmr-bug": "FMR mishandles a valid document: a crash, or content lost on the way in",
    "fmr-deviation": "the XSD declares it, and FMR's reader refuses it",
    "fmr-gap": "FMR has no reader for the construct at all",
    "fmr-rule": "a rule FMR enforces that neither the XSD nor the IM states",
    "registry-rule": "a rule the IM states, which FMR enforces as a registry and sdmxlib, a library, does not",
}


@frozen
class Adjustment:
    """One thing removed from one document, and why."""

    version: Version
    document: Document
    xpath: str
    kind: Kind
    reason: str


def _el(*names: str) -> str:
    """An XPath descending by local name, so namespaces need no prefixes."""
    return "//" + "/".join(f"*[local-name()='{name}']" for name in names)


_L = "local-name()"


def _both(document: Document, xpath: str, kind: Kind, reason: str) -> tuple[Adjustment, Adjustment]:
    """The same adjustment for 2.1 and 3.0, where FMR's two readers refuse alike."""
    return Adjustment("2.1", document, xpath, kind, reason), Adjustment("3.0", document, xpath, kind, reason)


ADJUSTMENTS: tuple[Adjustment, ...] = (
    # Rules the IM states, which FMR enforces as the registry maintaining artefacts.
    *_both(
        "maximal",
        _el("Codelist") + "[@isPartial='true']/@isPartial",
        "registry-rule",
        "FMR will not maintain a partial codelist: 'A \"partial\" ItemScheme cannot be maintained independently' "
        "(IM 3.5.3.1, census IM-3.5.3.1-1). The flag goes, not the codelist, which the fixture references",
    ),
    # Rules FMR enforces that neither the XSD nor the IM states.
    *_both(
        "maximal",
        _el("MetadataAttribute") + f"[@isPresentational='true']/*[{_L}='LocalRepresentation']",
        "fmr-rule",
        "FMR: 'presentational so can not have coded representation'. IM 7.3.4.2 says only that no value is "
        "expected for one; the maximal attribute sets both",
    ),
    *_both(
        "maximal",
        f"//*[{_L}='ContentConstraint' or {_L}='DataConstraint'][*[{_L}='CubeRegion']]/*[{_L}='ReleaseCalendar']",
        "fmr-rule",
        "FMR: 'Constraint with Release Calendar must not contain Cube Region restrictions' (HTTP 501)",
    ),
    *_both(
        "maximal",
        _el("StructureMap", "ComponentMap") + f"[preceding-sibling::*[{_L}='ComponentMap']]",
        "fmr-rule",
        "FMR: 'Duplicate Mapping for Components: DIM-DIM'. The maximal maps differ by the epoch, date-pattern "
        "and representation maps they name; neither XSD forbids the repeat",
    ),
    Adjustment(
        "3.0",
        "shape",
        _el("DataConstraint") + f"[not(*[{_L}='CubeRegion' or {_L}='DataKeySet'])]",
        "fmr-rule",
        "FMR: 'A constraint must have at least one exclude or include Restrictions'. The IM gives a "
        "constraint's content as optional; the shapes' constraints carry only an attachment",
    ),
    Adjustment(
        "2.1",
        "shape",
        _el("ContentConstraint") + f"[not(*[{_L}='CubeRegion' or {_L}='DataKeySet'])]",
        "fmr-rule",
        "as 3.0: FMR refuses a constraint with no restrictions",
    ),
    # FMR mishandles a valid document.
    Adjustment(
        "3.0",
        "maximal",
        _el("ValueList", "ValueItem") + f"[not(*[{_L}='Name'])]",
        "fmr-bug",
        "FMR's 3.0 reader throws NullPointerException ('nodeNameEnum is null') on a ValueItem with no Name, "
        "which the XSD makes optional. Inside a larger submission the crash truncates the read: FMR persisted "
        "the first 7 of 28 artefacts and reported success",
    ),
    Adjustment(
        "3.0",
        "maximal",
        _el("GeoGridCodelist", "GeoGridCode", "Parent"),
        "fmr-bug",
        "a GeoGridCode with a Parent loses its GeoCell in FMR ('Geogrid Code is missing required Geo Cell'); "
        "GeoGridCodeType adds GeoCell after the code's Parent, which is the order written",
    ),
    Adjustment(
        "3.0",
        "maximal",
        _el("DataStructureComponents") + f"//*[{_L}='ConceptRole']",
        "fmr-bug",
        "FMR's 3.0 reader loses its place after a ConceptRole and refuses the component's next element; the "
        "XSD declares ConceptRole on Dimension, Attribute and Measure. Its 2.1 reader resolves the same roles",
    ),
    Adjustment(
        "3.0",
        "maximal",
        _el("StructureMap", "DatePatternMap"),
        "fmr-bug",
        "FMR's 3.0 reader seems to take a DatePatternMap, then refuses every ComponentMap after it; "
        "StructureMapType declares DatePatternMap* before them",
    ),
    # The XSD declares it; FMR's reader refuses it.
    Adjustment(
        "3.0",
        "maximal",
        _el("DataKeySet", "Key", "Annotations"),
        "fmr-deviation",
        "DataKeyType restricts RegionType with common:Annotations first; FMR expects KeyValue or Component there. "
        "Unseen until #491 moved the key sets to a constraint of their own, which no adjustment removes",
    ),
    Adjustment(
        "3.0",
        "maximal",
        _el("DataKeySet", "Key", "Component") + f"[*[{_L}='TimeRange']]",
        "fmr-deviation",
        "DataComponentValueSetType is an optional choice of Value or TimeRange; FMR requires Value, so the "
        "component goes whole. Unseen until #491, too",
    ),
    *(
        Adjustment(
            "3.0",
            "maximal",
            _el(codelist, "CodelistExtension"),
            "fmr-deviation",
            f"{codelist}Type declares CodelistExtension, a GeoGridCodelist through its base type",
        )
        for codelist in ("GeographicCodelist", "GeoGridCodelist")
    ),
    *(
        Adjustment(
            "3.0",
            "shape",
            _el("Codelist", code),
            "fmr-deviation",
            "GeoRefCode is in Code's substitution group, so any codelist may hold a geographic code",
        )
        for code in ("GeoFeatureSetCode", "GeoGridCode")
    ),
    *(
        Adjustment(
            "3.0",
            document,
            _el("SentinelValue"),
            "fmr-deviation",
            "FMR's 3.0 reader refuses every SentinelValue; the XSD declares it on the text format types of "
            "every component and of a concept",
        )
        for document in ("maximal", "shape")
    ),
    *(
        Adjustment(
            "3.0",
            "maximal",
            _el(owner) + path,
            "fmr-deviation",
            f"FMR's 3.0 reader refuses com:Link {where}; IdentifiableType declares Link*",
        )
        for owner, path, where in (
            ("DataStructureComponents", f"//*[{_L}='Link']", "inside a DSD's components"),
            ("MetadataStructureComponents", f"//*[{_L}='Link']", "inside an MSD's components"),
            ("Hierarchy", f"/*//*[{_L}='Link']", "below a hierarchy (its codes and levels)"),
        )
    ),
    Adjustment(
        "3.0",
        "maximal",
        _el("CubeRegion", "Annotations"),
        "fmr-deviation",
        "FMR's 3.0 reader refuses Annotations on a CubeRegion; RegionType declares them and CubeRegionType keeps them",
    ),
    *_both(
        "maximal",
        _el("Concept", "ISOConceptReference"),
        "fmr-deviation",
        "FMR resolves an ISOConceptReference as an SDMX concept; it names an ISO 11179 one (IM 4.5.4.2), and "
        "the XSD types its parts as strings",
    ),
    Adjustment(
        "2.1",
        "shape",
        _el("Group") + f"[*[{_L}='AttachmentConstraint']]",
        "fmr-deviation",
        "2.1's GroupType is a choice of GroupDimension+ or AttachmentConstraint",
    ),
    Adjustment(
        "2.1",
        "maximal",
        _el("ProvisionAgreement") + f"[*[{_L}='StructureUsage']/Ref[@class='Metadataflow']]",
        "fmr-deviation",
        "2.1 carries a metadata provision agreement as a ProvisionAgreement on a Metadataflow (#421); "
        "StructureUsageReferenceType admits one, and FMR expects a Dataflow",
    ),
    # FMR has no reader for the construct.
    *(
        Adjustment(
            "3.0",
            document,
            _el(section),
            "fmr-gap",
            f"FMR 12.4.2 has no SDMX-ML 3.0 reader for {section} (HTTP 501)",
        )
        for document, section in (
            ("shape", "CategorySchemeMaps"),
            ("maximal", "ConceptSchemeMaps"),
            ("maximal", "OrganisationSchemeMaps"),
        )
    ),
    *(
        Adjustment(
            "3.0",
            "maximal",
            _el("StructureMap", element),
            "fmr-gap",
            f"FMR's 3.0 reader refuses a structure map's {element}, which StructureMapType declares",
        )
        for element in ("EpochMap",)
    ),
    Adjustment(
        "3.0",
        "maximal",
        _el("AttributeList", "MetadataAttributeUsage"),
        "fmr-gap",
        "FMR's 3.0 reader refuses MetadataAttributeUsage outright -- with a flat MSD, and with the usage's "
        "annotations, links and nested reference removed",
    ),
    Adjustment(
        "2.1",
        "shape",
        _el("ReportingYearStartDay"),
        "fmr-gap",
        "FMR reads no part of 2.1's ReportingYearStartDay: its Annotations, then its ConceptIdentity, are each "
        "'unexpected'",
    ),
    Adjustment(
        "2.1",
        "shape",
        _el("Constraints", "AttachmentConstraint"),
        "fmr-gap",
        "FMR reads no 2.1 AttachmentConstraint, which ConstraintsType declares",
    ),
)
