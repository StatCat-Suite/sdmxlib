"""The structure rules of a 2.1 → 3.x migration: what the target format cannot carry (GH-399).

Each rule reads what sdmxlib already knows about the target format, so the
plan states nothing the writer would not do: `formats.gaps.dropped` for the
classes the target lacks, `formats.gaps.unrepresentable` for the fields it
loses, and `Metadataflow.with_derived_targets` for the MSD remodel.

- **format.dropped** (change): an artefact of a class the target lacks, such
  as a `StructureSet` or a `HierarchicalCodelist`, is left out; what it held
  is written without it.
- **format.converted** (change): an artefact written as another class, such
  as an `AttachmentConstraint` as the Allowed `DataConstraint` it equals. The
  writer converts it, so the change makes no edit.
- **metadataflow.targets** (change, or blocked): 3.x requires a metadataflow
  to name its targets, which 2.1 put in the MSD. The targets its MSD implies
  are set on the flow; a flow whose targets cannot be derived is blocked,
  with each reason.
- **msd.target-layer** (change): the 2.1 MSD's metadata targets and report
  structures, which 3.x replaced with a flat attribute list and the flow's
  targets. The writer leaves them out, so the change makes no edit.
- **format.loss** (question): any other field the target cannot carry, such
  as a `DataConstraint`'s reference period. Accepted, the artefact is written
  without it; or the artefact is left out.
- **structure.measure-dimension** (question): 3.x has no MeasureDimension.
  It becomes a `Dimension` of the same id enumerated by a new codelist, one
  code per concept of its concept scheme and versioned as it is (``dimension``, recommended: the
  data keeps its shape, and constraints keep their key values); or one
  `Measure` per concept, with the primary measure's representation, in place
  of the primary measure (``measures``), when the constraints on the DSD and
  its dataflows lose their key values on it and attributes and groups stop
  naming it. Blocked when the concept scheme is not in the message, or the
  codelist's id is taken.
"""

from collections.abc import Generator, Iterator

import attrs

from sdmxlib.formats.gaps import Gap, dropped, unrepresentable
from sdmxlib.migration._types import Add, Blocked, Change, Omit, Option, Question, SetField, Target
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.datastructure import (
    Attribute,
    AttributeUsage,
    DataStructure,
    Dimension,
    Measure,
    MeasureDimension,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn

type Finding = Change | Question | Blocked

#: Each rule here that asks, and the option ids its questions take.
QUESTIONS: dict[str, tuple[str, ...]] = {
    "format.loss": ("accept", "omit"),
    "structure.measure-dimension": ("dimension", "measures"),
}

#: The gaps another rule covers, by artefact class (None for every class): the
#: version rules (`legacy_is_final`), a whole class (`sdmx_class`, reported as
#: dropped or converted), the MSD's target layer, a flow's targets, and a
#: MeasureDimension.
_COVERED: dict[str | None, frozenset[str]] = {
    None: frozenset({"legacy_is_final", "sdmx_class"}),
    "MetadataStructure": frozenset({"targets", "report_structures"}),
    "Metadataflow": frozenset({"targets"}),
    "DataStructure": frozenset({"measure_dimension"}),
}


def _covered(artefact_class: str, gap: Gap) -> bool:
    return gap.field in _COVERED[None] or gap.field in _COVERED.get(artefact_class, frozenset())


def findings(msg: StructureMessage, target: Target) -> Iterator[Finding]:
    """Every structure finding for moving *msg* to *target*."""
    gone = yield from _dropped(msg, target)
    for artefact in msg.all_artefacts():
        urn = SdmxUrn.parse(str(artefact_urn(artefact)))
        if str(urn) in gone:
            continue
        gaps = unrepresentable(artefact, target.version)
        yield from _converted(urn, gaps)
        if urn.artefact_class == "MetadataStructure":
            yield from _target_layer(urn, gaps)
        yield from _losses(urn, [gap for gap in gaps if not _covered(urn.artefact_class, gap)])
    yield from _metadataflow_targets(msg, gone)
    yield from _measure_dimensions(msg, gone)


def _dropped(msg: StructureMessage, target: Target) -> Generator[Finding, None, set[str]]:
    """`format.dropped` for each artefact the target leaves out; returns their URNs."""
    gone: set[str] = set()
    for drop in dropped(msg, target.version):
        gone.add(drop.urn)
        try:
            urn = SdmxUrn.parse(drop.urn)
        except ValueError:
            # An availability constraint, which has no URN to omit by.
            edits: tuple[Omit, ...] = ()
        else:
            edits = (Omit(urn=urn),)
        yield Change(
            rule="format.dropped", urn=drop.urn, path="", before=None, after=None, reason=drop.reason, edits=edits
        )
    return gone


def _converted(urn: SdmxUrn[object], gaps: tuple[Gap, ...]) -> Iterator[Finding]:
    for gap in gaps:
        if gap.field == "sdmx_class":
            yield Change(
                rule="format.converted", urn=str(urn), path="", before=None, after=None, reason=gap.reason, edits=()
            )


def _target_layer(urn: SdmxUrn[object], gaps: tuple[Gap, ...]) -> Iterator[Finding]:
    fields = [gap for gap in gaps if gap.field in {"targets", "report_structures"}]
    if fields:
        yield Change(
            rule="msd.target-layer",
            urn=str(urn),
            path=", ".join(gap.field for gap in fields),
            before=None,
            after=None,
            reason=fields[0].reason,
            edits=(),
        )


def _losses(urn: SdmxUrn[object], lost: list[Gap]) -> Iterator[Finding]:
    if not lost:
        return
    fields = ", ".join(gap.field for gap in lost)
    yield Question(
        key=f"format.loss@{urn}",
        rule="format.loss",
        urn=str(urn),
        question=(
            f"{urn.agency}:{urn.id}({urn.version}) carries what the target cannot: "
            + "; ".join(f"{gap.field} ({gap.reason})" for gap in lost)
            + ". Write it without them?"
        ),
        options=(
            Option(id="accept", consequence=f"written without {fields}", edits=()),
            Option(
                id="omit",
                consequence="left out of the result; references to it keep naming it",
                edits=(Omit(urn=urn),),
            ),
        ),
        recommended="accept",
    )


def _metadataflow_targets(msg: StructureMessage, gone: set[str]) -> Iterator[Finding]:
    for flow in msg.metadataflows:
        urn = SdmxUrn.parse(str(artefact_urn(flow)))
        if flow.targets or str(urn) in gone:
            continue
        misfits = flow.target_misfits(msg.metadata_structures)
        if misfits:
            yield Blocked(
                rule="metadataflow.targets",
                urn=str(urn),
                reason=(
                    "3.x requires a metadataflow to name its targets, and none can be derived: " + "; ".join(misfits)
                ),
            )
            continue
        targets = flow.with_derived_targets(msg.metadata_structures).targets
        yield Change(
            rule="metadataflow.targets",
            urn=str(urn),
            path="targets",
            before=None,
            after=", ".join(targets),
            reason="3.x requires a metadataflow to name its targets, which 2.1 declared in its metadata structure",
            edits=(SetField(urn=urn, name="targets", value=targets),),
        )


def _measure_dimensions(msg: StructureMessage, gone: set[str]) -> Iterator[Finding]:
    for dsd in msg.data_structures:
        urn = SdmxUrn.parse(str(artefact_urn(dsd)))
        measure_dimension = dsd.measure_dimension
        if measure_dimension is None or str(urn) in gone:
            continue
        yield _measure_dimension(msg, dsd, urn, measure_dimension)


def _measure_dimension(
    msg: StructureMessage, dsd: DataStructure, urn: SdmxUrn[object], measure_dimension: MeasureDimension
) -> Finding:
    scheme = _scheme_of(msg, measure_dimension)
    if scheme is None:
        return Blocked(
            rule="structure.measure-dimension",
            urn=str(urn),
            reason=(
                f"SDMX 3.x has no MeasureDimension, and {dsd.id} has {measure_dimension.id}, whose concept scheme "
                "is not in the message; add it, so the measure dimension can be remodelled as a dimension or as "
                "measures"
            ),
        )
    codelist = _codelist_of(scheme)
    # Any version: the version rules may move this one onto a held one.
    taken = {(held.agency_id, held.id) for held in msg.codelists}
    if (codelist.agency_id, codelist.id) in taken:
        return Blocked(
            rule="structure.measure-dimension",
            urn=str(urn),
            reason=(
                f"SDMX 3.x has no MeasureDimension, and remodelling {dsd.id}'s {measure_dimension.id} as a "
                f"dimension needs a codelist {codelist.agency_id}:{codelist.id}, which the message "
                "already holds at a version"
            ),
        )
    as_dimension = attrs.evolve(
        dsd,
        dimensions=[
            *dsd.dimensions,
            Dimension(
                id=measure_dimension.id,
                concept=measure_dimension.concept,
                representation=Ref.to(codelist),
                annotations=measure_dimension.annotations,
                concept_role=measure_dimension.concept_role,
            ),
        ],
        measure_dimension=None,
    )
    as_measures, constraints = _as_measures(msg, dsd, scheme, measure_dimension.id)
    concepts = ", ".join(scheme.concepts.ids())
    return Question(
        key=f"structure.measure-dimension@{urn}",
        rule="structure.measure-dimension",
        urn=str(urn),
        question=(
            f"SDMX 3.x has no MeasureDimension, and {dsd.agency_id}:{dsd.id}({dsd.version}) has "
            f"{measure_dimension.id}, enumerated by {scheme.agency_id}:{scheme.id}({scheme.version}). Remodel it "
            "as a dimension, or as one measure per concept?"
        ),
        options=(
            Option(
                id="dimension",
                consequence=(
                    f"{measure_dimension.id} becomes a dimension enumerated by the new codelist "
                    f"{codelist.agency_id}:{codelist.id}({codelist.version}), with the codes {concepts}"
                ),
                edits=(
                    Add(artefact=codelist, versioned_as=SdmxUrn.parse(str(artefact_urn(scheme)))),
                    Add(artefact=as_dimension),
                ),
            ),
            Option(
                id="measures",
                consequence=(
                    f"the measures {concepts} take the place of {', '.join(dsd.measures.ids()) or 'no measure'}; "
                    f"{len(constraints)} constraint(s) lose their key values on {measure_dimension.id}"
                ),
                edits=(Add(artefact=as_measures), *(Add(artefact=constraint) for constraint in constraints)),
            ),
        ),
        recommended="dimension",
    )


def _scheme_of(msg: StructureMessage, measure_dimension: MeasureDimension) -> ConceptScheme | None:
    representation = measure_dimension.representation
    if not isinstance(representation, Ref):
        return None
    wanted = str(representation.urn)
    return next((scheme for scheme in msg.concept_schemes if str(artefact_urn(scheme)) == wanted), None)


def _codelist_of(scheme: ConceptScheme) -> Codelist:
    """A codelist with a code per concept of *scheme*, named as it is, at its version."""
    return Codelist(
        id=f"CL_{scheme.id}",
        maintainer=scheme.agency_id,
        version=scheme.version,
        name=scheme.name,
        description=scheme.description,
        codes=[
            Code(id=concept.id, name=concept.name, description=concept.description, parent_id=concept.parent_id)
            for concept in scheme.concepts
        ],
    )


def _as_measures(
    msg: StructureMessage, dsd: DataStructure, scheme: ConceptScheme, dimension_id: str
) -> tuple[DataStructure, list[DataConstraint]]:
    """*dsd* with a measure per concept of *scheme*, and the constraints on it without key values on *dimension_id*."""
    primary = dsd.measures[0] if len(dsd.measures) else None
    measures = [
        Measure(
            id=concept.id,
            concept=scheme.ref_to(concept.id),
            representation=primary.representation if primary is not None else None,
            usage=primary.usage if primary is not None else AttributeUsage.OPTIONAL,
        )
        for concept in scheme.concepts
    ]
    remodelled = attrs.evolve(
        dsd,
        measure_dimension=None,
        measures=measures,
        attributes=[_relinked(attribute, dimension_id, primary, measures) for attribute in dsd.attributes],
        groups=(
            None
            if dsd.groups is None
            else [
                attrs.evolve(group, dimension_ids=[d for d in group.dimension_ids if d != dimension_id])
                for group in dsd.groups
            ]
        ),
    )
    dsd_urn = str(artefact_urn(dsd))
    flows = {
        str(artefact_urn(flow))
        for flow in msg.dataflows
        if flow.structure is not None and str(flow.structure.urn) == dsd_urn
    }
    constraints = [
        _without_key(constraint, dimension_id)
        for constraint in msg.constraints
        if any(str(ref.urn) == dsd_urn for ref in constraint.attachment.data_structures)
        or any(str(ref.urn) in flows for ref in constraint.attachment.dataflows)
    ]
    return remodelled, [c for c in constraints if c is not None]


def _relinked(attribute: Attribute, dimension_id: str, primary: Measure | None, measures: list[Measure]) -> Attribute:
    """*attribute* without *dimension_id*, relating to every new measure where it related to *primary*."""
    related = attribute.related_dimensions
    if related and dimension_id in related:
        attribute = attrs.evolve(attribute, related_dimensions=[d for d in related if d != dimension_id])
    named = attribute.related_measures
    if primary is not None and named and primary.id in named:
        replaced = [m for m in named if m != primary.id]
        attribute = attrs.evolve(attribute, related_measures=(*replaced, *(m.id for m in measures)))
    return attribute


def _without_key(constraint: DataConstraint, dimension_id: str) -> DataConstraint | None:
    """*constraint* without its key values on *dimension_id*, or None when it has none."""
    names = any(key.dimension_id == dimension_id for region in constraint.regions for key in region.keys) or any(
        component == dimension_id
        for key_set in constraint.data_key_sets
        for key in key_set.keys
        for component, _ in key.values
    )
    if not names:
        return None
    return attrs.evolve(
        constraint,
        regions=[
            attrs.evolve(region, keys=[key for key in region.keys if key.dimension_id != dimension_id])
            for region in constraint.regions
        ],
        data_key_sets=[
            attrs.evolve(
                key_set,
                keys=[
                    attrs.evolve(key, values=[pair for pair in key.values if pair[0] != dimension_id])
                    for key in key_set.keys
                ],
            )
            for key_set in constraint.data_key_sets
        ],
    )
