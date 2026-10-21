"""What the pysdmx oracle removes from a document before pysdmx will take it, and why (#457).

pysdmx reads an SDMX-ML 3.1 document into its own model and writes it back.
Its reader skips an artefact class it does not model, and the replay test
reports that as a difference -- but it raises on an element or attribute it
does not know *inside* a class it does model, refusing the whole document.
Each such refusal is answered here by one `Adjustment`: an XPath naming what
is removed, a `Kind` saying whose rule it is, and the reason.

The ledger is checked both ways by the replay test: an adjustment must match
something in the document, and the recording must show pysdmx failing on the
document without it -- so an adjustment a newer pysdmx stops needing is
reported, as is a failure no adjustment answers.
"""

from typing import Literal

from attrs import frozen

type Document = Literal["maximal", "shape"]
type Kind = Literal["pysdmx-gap", "pysdmx-bug", "pysdmx-writer"]

#: What each kind of adjustment says about whose rule it is.
KINDS: dict[Kind, str] = {
    "pysdmx-gap": "pysdmx's model has no field for it, and its reader raises on the unknown key",
    "pysdmx-bug": "pysdmx's reader fails on a valid construct it does model",
    "pysdmx-writer": "pysdmx reads it, then its writer refuses to write it",
}


@frozen
class Adjustment:
    """One thing removed from one SDMX-ML 3.1 document, and why."""

    document: Document
    xpath: str
    kind: Kind
    reason: str


_L = "local-name()"


def _el(*names: str) -> str:
    """An XPath descending by local name, so namespaces need no prefixes."""
    return "//" + "/".join(f"*[{_L}='{name}']" for name in names)


_MAPS = f"[{_L}='FixedValueMap' or {_L}='DatePatternMap']"
_COMPONENTS = f"//*[{_L}='DimensionList' or {_L}='AttributeList' or {_L}='MeasureList']/*/@uri"

ADJUSTMENTS: tuple[Adjustment, ...] = (
    # Fields pysdmx's model does not have, on classes it does.
    Adjustment(
        "maximal",
        _el("Annotation", "AnnotationValue"),
        "pysdmx-gap",
        "pysdmx's Annotation has no value; SDMX 3.0 added AnnotationValue",
    ),
    Adjustment("maximal", _el("Contact", "X400"), "pysdmx-gap", "pysdmx's Contact has no X.400 address"),
    Adjustment(
        "maximal",
        "//@isPartialLanguage",
        "pysdmx-gap",
        "pysdmx's maintainable artefacts have no isPartialLanguage, new in SDMX 3.1",
    ),
    *(
        Adjustment(document, _el("Codelist", "CodelistExtension"), "pysdmx-gap", "pysdmx's Codelist has no extensions")
        for document in ("maximal", "shape")
    ),
    *(
        Adjustment(
            "shape",
            _el("Codelist", code),
            "pysdmx-gap",
            "pysdmx's Codelist holds no geographic code; GeoRefCode is in Code's substitution group",
        )
        for code in ("GeoFeatureSetCode", "GeoGridCode")
    ),
    Adjustment(
        "maximal",
        _el("Concept", "ISOConceptReference"),
        "pysdmx-gap",
        "pysdmx's Concept has no ISO concept reference",
    ),
    Adjustment("maximal", _COMPONENTS, "pysdmx-gap", "pysdmx's Component has no uri"),
    Adjustment(
        "maximal",
        "//@evolvingStructure",
        "pysdmx-gap",
        "pysdmx's DataStructureDefinition has no evolvingStructure, new in SDMX 3.1",
    ),
    Adjustment(
        "maximal",
        _el("Dataflow", "DimensionConstraint"),
        "pysdmx-gap",
        "pysdmx's Dataflow has no dimension constraint, new in SDMX 3.1",
    ),
    Adjustment(
        "maximal",
        _el("MetadataProvisionAgreement", "Target"),
        "pysdmx-gap",
        "pysdmx's MetadataProvisionAgreement has no targets, new in SDMX 3.1",
    ),
    Adjustment(
        "maximal",
        f"//*{_MAPS}/*[{_L}='Annotations']",
        "pysdmx-gap",
        "pysdmx's FixedValueMap and DatePatternMap have no annotations",
    ),
    Adjustment(
        "maximal",
        _el("FixedValueMap") + f"[*[{_L}='Source']]",
        "pysdmx-gap",
        "pysdmx's FixedValueMap fixes a target only; FixedValueMapType is a choice of Source or Target",
    ),
    Adjustment("maximal", _el("DatePatternMap") + "/@uri", "pysdmx-gap", "pysdmx's DatePatternMap has no uri"),
    Adjustment("maximal", _el("DatePatternMap", "Link"), "pysdmx-gap", "pysdmx's DatePatternMap has no links"),
    Adjustment(
        "maximal",
        _el("DatePatternMap") + f"[*[{_L}='FrequencyDimension']]",
        "pysdmx-gap",
        "pysdmx's DatePatternMap needs a target frequency; DatePatternMapType is a choice of that or a "
        "frequency dimension with mapped frequencies",
    ),
    *(
        Adjustment("maximal", _el("StructureMap", element), "pysdmx-gap", f"pysdmx's StructureMap has no {element}")
        for element in ("EpochMap", "FrequencyFormatMapping")
    ),
    # A valid construct pysdmx models, and fails on.
    Adjustment(
        "maximal",
        _el("AttributeRelationship", "Dimension") + "/@optional",
        "pysdmx-bug",
        "pysdmx takes an attribute's related Dimension with an attribute for 2.1's <Dimension><Ref/>, and "
        "fails with KeyError 'Ref'; 3.0 and 3.1 declare @optional on it",
    ),
    Adjustment(
        "maximal",
        _el("Group", "Annotations"),
        "pysdmx-bug",
        "pysdmx's Group has annotations, but its reader passes a group's Annotations element to it unconverted "
        "('Unexpected keyword argument')",
    ),
    # pysdmx reads it, and its writer refuses it.
    Adjustment(
        "maximal",
        _el("ValueList", "ValueItem") + f"[not(*[{_L}='Name'])]",
        "pysdmx-writer",
        "pysdmx's writer requires every item's Name ('Name is required for NameableArtefact'); ValueItemType "
        "makes it optional. FMR's reader fails on the same item",
    ),
)
