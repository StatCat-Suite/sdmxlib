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
- **structure.measure-dimension** (blocked): 3.x has no MeasureDimension.
"""

from collections.abc import Generator, Iterator

from sdmxlib.formats.gaps import Gap, dropped, unrepresentable
from sdmxlib.migration._types import Blocked, Change, Omit, Option, Question, SetField, Target
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import artefact_urn
from sdmxlib.model.urn import SdmxUrn

type Finding = Change | Question | Blocked

#: Each rule here that asks, and the option ids its questions take.
QUESTIONS: dict[str, tuple[str, ...]] = {"format.loss": ("accept", "omit")}

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
        if dsd.measure_dimension is None or str(urn) in gone:
            continue
        yield Blocked(
            rule="structure.measure-dimension",
            urn=str(urn),
            reason=(
                f"SDMX 3.x has no MeasureDimension, and {dsd.id} has {dsd.measure_dimension.id}; remodel it as a "
                "dimension enumerated by a codelist, or as one measure per concept, before migrating"
            ),
        )
