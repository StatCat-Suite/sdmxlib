"""The SDMX-ML 3.0 constructs FMR 12.5.0 refuses, and how a plan takes them out (GH-399).

Each `Refusal` is one construct FMR's 3.0 reader or registry rules refuse,
named by the XPath its refusal was recorded under: the FMR oracle's ledger
(`bench/fmr/ledger.py`) removed exactly that from a document before FMR would
take it, and recorded FMR refusing the document without the removal.
`tests/integration/test_fmr_refusals.py` holds this table to the ledger's
12.5.0 adjustments for 3.0, both ways.

A plan finds a refusal by writing each artefact as SDMX-ML 3.0 and running
the XPaths over it, and asks one question per artefact and refusal:

- ``strip``: remove what the XPaths match and read the artefact back, which is
  what the ledger did. Offered where the artefact survives without it.
- ``omit``: leave the artefact out.
- ``keep``: send it, and FMR refuses it.

The recommendation is ``strip`` where what goes is presentational (links,
annotations) or a value the IM says is not expected, ``omit`` where FMR cannot
hold the artefact at all, and none where a person has to weigh a real loss.

One refusal needs more than an XPath. FMR refuses two component maps for the
same source and target ("Duplicate Mapping for Components: DIM-DIM"), which is
a comparison between siblings; `_duplicate_maps` makes it on the model.
"""

from collections.abc import Callable, Iterator
from functools import partial

import attrs
from attrs import frozen
from lxml import etree

from sdmxlib.formats import parse, to_xml
from sdmxlib.migration._types import Omit, Option, Question, Target, Transform
from sdmxlib.model.mapping import StructureMap
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.model.urn import SdmxUrn

type Recommendation = str | None


def _el(*names: str) -> str:
    """An XPath descending by local name, as the ledger writes them."""
    return "//" + "/".join(f"*[local-name()='{name}']" for name in names)


_L = "local-name()"


@frozen
class Refusal:
    """One construct FMR 12.5.0 refuses in SDMX-ML 3.0.

    Attributes:
        rule: The plan rule, ``fmr.<construct>``.
        xpaths: What the ledger removed, so what a plan looks for and strips.
        reason: FMR's refusal, or why the construct is refused.
        strip: Whether the artefact survives the removal.
        recommended: The option to pick when in doubt, or None.
    """

    rule: str
    xpaths: tuple[str, ...]
    reason: str
    strip: bool
    recommended: Recommendation


REFUSALS: tuple[Refusal, ...] = (
    Refusal(
        "fmr.partial-codelist",
        (_el("Codelist") + "[@isPartial='true']/@isPartial",),
        "FMR will not maintain a partial codelist ('A \"partial\" ItemScheme cannot be maintained independently')",
        strip=True,
        recommended="omit",
    ),
    Refusal(
        "fmr.presentational-representation",
        (_el("MetadataAttribute") + f"[@isPresentational='true']/*[{_L}='LocalRepresentation']",),
        "FMR refuses a presentational metadata attribute with a representation ('presentational so can not "
        "have coded representation'); the IM expects no value for one",
        strip=True,
        recommended="strip",
    ),
    Refusal(
        "fmr.release-calendar",
        (f"//*[{_L}='ContentConstraint' or {_L}='DataConstraint'][*[{_L}='CubeRegion']]/*[{_L}='ReleaseCalendar']",),
        "FMR refuses a constraint with both a release calendar and cube regions ('Constraint with Release "
        "Calendar must not contain Cube Region restrictions')",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.empty-constraint",
        (f"//*[{_L}='DataConstraint'][not(*[{_L}='CubeRegion' or {_L}='DataKeySet'])]",),
        "FMR refuses a constraint with no cube region or key set ('A constraint must have at least one exclude "
        "or include Restrictions')",
        strip=False,
        recommended="omit",
    ),
    Refusal(
        "fmr.nameless-value-item",
        (_el("ValueList", "ValueItem") + f"[not(*[{_L}='Name'])]",),
        "FMR's 3.0 reader fails on a value item with no name, which the XSD makes optional",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.geo-grid-parent",
        (_el("GeoGridCodelist", "GeoGridCode", "Parent"),),
        "FMR loses a geo-grid code's GeoCell when the code has a parent ('Geogrid Code is missing required Geo Cell')",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.concept-role",
        (_el("DataStructureComponents") + f"//*[{_L}='ConceptRole']",),
        "FMR's 3.0 reader loses its place after a ConceptRole and refuses the component's next element",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.date-pattern-map",
        (_el("StructureMap", "DatePatternMap"),),
        "FMR's 3.0 reader takes a DatePatternMap and then refuses every ComponentMap after it",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.data-key-annotations",
        (_el("DataKeySet", "Key", "Annotations"),),
        "FMR's 3.0 reader refuses annotations on a data key, which the XSD declares first",
        strip=True,
        recommended="strip",
    ),
    Refusal(
        "fmr.data-key-time-range",
        (_el("DataKeySet", "Key", "Component") + f"[*[{_L}='TimeRange']]",),
        "FMR's 3.0 reader requires a Value on a data key's component, which the XSD lets be a TimeRange",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.geo-codelist-extension",
        (_el("GeographicCodelist", "CodelistExtension"), _el("GeoGridCodelist", "CodelistExtension")),
        "FMR's 3.0 reader refuses a CodelistExtension on a geographic or geo-grid codelist",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.geo-code-in-codelist",
        (_el("Codelist", "GeoFeatureSetCode"), _el("Codelist", "GeoGridCode")),
        "FMR's 3.0 reader refuses a geographic code in a plain codelist, which the XSD's substitution group allows",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.sentinel-value",
        (_el("SentinelValue"),),
        "FMR's 3.0 reader refuses every SentinelValue",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.component-link",
        (
            _el("DataStructureComponents") + f"//*[{_L}='Link']",
            _el("MetadataStructureComponents") + f"//*[{_L}='Link']",
            _el("Hierarchy") + f"/*//*[{_L}='Link']",
        ),
        "FMR's 3.0 reader refuses a com:Link inside a DSD's or MSD's components, or below a hierarchy",
        strip=True,
        recommended="strip",
    ),
    Refusal(
        "fmr.cube-region-annotations",
        (_el("CubeRegion", "Annotations"),),
        "FMR's 3.0 reader refuses annotations on a cube region",
        strip=True,
        recommended="strip",
    ),
    Refusal(
        "fmr.iso-concept-reference",
        (_el("Concept", "ISOConceptReference"),),
        "FMR resolves an ISOConceptReference as an SDMX concept, where it names an ISO 11179 one",
        strip=True,
        recommended=None,
    ),
    Refusal(
        "fmr.no-reader",
        (_el("CategorySchemeMaps"), _el("ConceptSchemeMaps"), _el("OrganisationSchemeMaps")),
        "FMR has no SDMX-ML 3.0 reader for category, concept or organisation scheme maps (HTTP 501)",
        strip=False,
        recommended="omit",
    ),
    Refusal(
        "fmr.epoch-map",
        (_el("StructureMap", "EpochMap"),),
        "FMR's 3.0 reader refuses a structure map's EpochMap",
        strip=True,
        recommended=None,
    ),
)

#: The refusal `_duplicate_maps` finds on the model, with the ledger XPath its
#: recording was made under (it removed every component map after the first).
DUPLICATE_MAPS = Refusal(
    "fmr.duplicate-component-maps",
    (_el("StructureMap", "ComponentMap") + f"[preceding-sibling::*[{_L}='ComponentMap']]",),
    "FMR refuses two component maps for the same source and target ('Duplicate Mapping for Components')",
    strip=True,
    recommended=None,
)

#: Each refusal rule that asks, and the options its questions take.
QUESTIONS: dict[str, tuple[str, ...]] = {
    refusal.rule: ("strip", "omit", "keep") if refusal.strip else ("omit", "keep")
    for refusal in (*REFUSALS, DUPLICATE_MAPS)
}

type Artefact = SupportsUrn


def findings(msg: StructureMessage, target: Target) -> Iterator[Question]:
    """A question per artefact of *msg* and construct FMR refuses in it, for an FMR target."""
    if target.registry is None or not target.registry.startswith("fmr-"):
        return
    for artefact in msg.all_artefacts():
        tree = _written(artefact)
        for refusal in REFUSALS:
            if tree is not None and any(tree.xpath(xpath) for xpath in refusal.xpaths):
                yield _question(refusal, artefact)
        if isinstance(artefact, StructureMap) and _duplicate_maps(artefact):
            yield _question(DUPLICATE_MAPS, artefact)


def _question(refusal: Refusal, artefact: Artefact) -> Question:
    urn = SdmxUrn.parse(str(artefact_urn(artefact)))
    options = [
        *(
            [Option(id="strip", consequence="written without it", edits=(Transform(urn=urn, name=refusal.rule),))]
            if refusal.strip
            else []
        ),
        Option(id="omit", consequence="left out of the result", edits=(Omit(urn=urn),)),
        Option(id="keep", consequence="sent as it is, and refused by FMR", edits=()),
    ]
    return Question(
        key=f"{refusal.rule}@{urn}",
        rule=refusal.rule,
        urn=str(urn),
        question=f"{urn.agency}:{urn.id}({urn.version}): {refusal.reason}. What should happen to it?",
        options=tuple(options),
        recommended=refusal.recommended,
    )


def _written(artefact: Artefact) -> etree._Element | None:
    """*artefact* alone as SDMX-ML 3.0, parsed; None when 3.0 has nothing to write for it."""
    body = to_xml(StructureMessage.containing(artefact), version="3.0", allow={"non-ncname-parent"}, validate=False)
    root = etree.fromstring(body)
    structures = root.find("{*}Structures")
    return root if structures is not None and len(structures) else None


def _duplicate_maps(structure_map: StructureMap) -> bool:
    pairs = [(component.sources, component.targets) for component in structure_map.maps]
    return len(pairs) != len(set(pairs))


def _strip_xpaths(xpaths: tuple[str, ...], artefact: Artefact) -> Artefact:
    """*artefact* without what *xpaths* match in its 3.0 form, read back."""
    body = to_xml(StructureMessage.containing(artefact), version="3.0", allow={"non-ncname-parent"}, validate=False)
    root = etree.fromstring(body)
    for xpath in xpaths:
        hits = root.xpath(xpath)
        for hit in hits if isinstance(hits, list) else []:
            if isinstance(hit, etree._ElementUnicodeResult) and hit.is_attribute:
                owner, name = hit.getparent(), hit.attrname
                if isinstance(owner, etree._Element) and name is not None:
                    del owner.attrib[name]
            elif isinstance(hit, etree._Element) and (parent := hit.getparent()) is not None:
                parent.remove(hit)
    read = parse(etree.tostring(root))
    urn = str(artefact_urn(artefact))
    for candidate in read.all_artefacts() if isinstance(read, StructureMessage) else []:
        if str(artefact_urn(candidate)) == urn and isinstance(candidate, type(artefact)):
            return candidate
    msg = f"{urn} did not survive stripping; offer omit for this refusal instead"
    raise ValueError(msg)


def _dedupe_maps(artefact: Artefact) -> Artefact:
    """A structure map with each source and target pair mapped once, the first kept."""
    if not isinstance(artefact, StructureMap):
        return artefact
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    kept = []
    for component in artefact.maps:
        pair = (component.sources, component.targets)
        if pair not in seen:
            seen.add(pair)
            kept.append(component)
    return attrs.evolve(artefact, maps=kept)


#: The transform each ``strip`` option names.
TRANSFORMS: dict[str, Callable[[Artefact], Artefact]] = {
    **{refusal.rule: partial(_strip_xpaths, refusal.xpaths) for refusal in REFUSALS if refusal.strip},
    DUPLICATE_MAPS.rule: _dedupe_maps,
}
