"""The FMR rules of a migration: what Fusion Metadata Registry 12.5.0 does beyond the XSD (GH-399).

Each rule states a behaviour recorded against FMR 12.5.0 by
`bench/fmr/registry_rules.py` and replayed by
`tests/integration/test_fmr_registry_rules.py`, so the plan predicts what FMR
will do before anything is sent:

- **fmr.actual-constraint** (question): FMR refuses every `Actual`
  DataConstraint ("Submission of DataConstraints with Role specified as
  'Actual' is not supported"). Left out (recommended), or kept and refused.
- **fmr.next-major** (question): FMR judges each final version of a codelist
  against the final version before it, and refuses a later one that removes
  codes unless it is the next major ("Version is lower then the expected
  version of 2.0.0"). Moved to that major (recommended), which overrides the
  version rule's move; or the earlier version left out; or kept and refused.
  Blocked when the next major is a version the message already holds.
- **fmr.related-dimensions** (change, no edit): FMR serves an attribute's
  related dimensions in the DSD's order.
- **fmr.monolingual** (change, no edit): FMR drops a metadata attribute's
  ``isMultiLingual="false"`` TextFormat, and 3.0 reads one with none as
  multilingual.
- **fmr.id-case** (question): FMR upper-cases a maintainable id with
  lower-case letters (``CL_low`` is stored as ``CL_LOW``), and SDMX ids are
  case-sensitive, so a reference to the original stops resolving. Renamed to
  the upper-cased id with every reference following (recommended), or sent
  as it is for FMR to rename.
- **fmr.id-case-clash** (blocked): two artefacts of one class, agency and
  version whose ids differ only by case become one URN in FMR. At a legacy
  version the second submission replaces the first, losing it silently; at a
  final semantic version FMR refuses the second ("Semantically versioned
  structure can not be modified").

`Target.fmr` also allows the deviation FMR shares with .Stat NSI: a code whose
parent is not an NCName (``non-ncname-parent``), which FMR accepts and serves
in SDMX-ML 3.0.0 though the XSD forbids it.
"""

import re
from collections.abc import Iterable, Iterator
from itertools import pairwise

from sdmxlib.migration._types import Blocked, Change, Move, Omit, Option, Question, Target
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.constraint import ConstraintType
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataAttribute
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.version import version_key

type Finding = Change | Question | Blocked

#: Each rule here that asks, and the option ids its questions take.
QUESTIONS: dict[str, tuple[str, ...]] = {
    "fmr.actual-constraint": ("omit", "keep"),
    "fmr.next-major": ("major", "omit-earlier", "keep"),
    "fmr.id-case": ("rename", "accept"),
}

_LEGACY = re.compile(r"^(0|[1-9]\d*)(\.(0|[1-9]\d*))?$")


def findings(msg: StructureMessage, plan_target: Target) -> Iterator[Finding]:
    """Every FMR finding for *msg*, when the target is an FMR release."""
    if plan_target.registry is None or not plan_target.registry.startswith("fmr-"):
        return
    yield from _actual_constraints(msg)
    yield from _next_major(msg)
    yield from _related_dimensions(msg)
    yield from _monolingual(msg)
    yield from _id_case(msg)


def _urn(artefact: SupportsUrn) -> SdmxUrn[object]:
    return SdmxUrn.parse(str(artefact_urn(artefact)))


def _actual_constraints(msg: StructureMessage) -> Iterator[Finding]:
    for constraint in msg.constraints:
        if constraint.constraint_type is not ConstraintType.ACTUAL:
            continue
        urn = _urn(constraint)
        yield Question(
            key=f"fmr.actual-constraint@{urn}",
            rule="fmr.actual-constraint",
            urn=str(urn),
            question=f"FMR refuses every Actual DataConstraint, and {urn.agency}:{urn.id} is one. Leave it out?",
            options=(
                Option(id="omit", consequence="left out of the result", edits=(Omit(urn=urn),)),
                Option(id="keep", consequence="sent, and refused by FMR", edits=()),
            ),
            recommended="omit",
        )


def _final_semantic(codelist: Codelist) -> str | None:
    """The 3.x version a codelist moves to as a final one, or None when it is not final."""
    version = codelist.version
    if _LEGACY.match(version):
        if not codelist.legacy_is_final:
            return None
        parts = version.split(".")
        return ".".join([*parts, *["0"] * (3 - len(parts))])
    if "-" in version or codelist.legacy_is_final is False:
        return None
    return version


def _next_major(msg: StructureMessage) -> Iterator[Finding]:
    by_identity: dict[tuple[str, str], list[tuple[str, Codelist]]] = {}
    for codelist in msg.codelists:
        semantic = _final_semantic(codelist)
        if semantic is not None:
            by_identity.setdefault((codelist.agency_id, codelist.id), []).append((semantic, codelist))
    for finals in by_identity.values():
        ordered = sorted(finals, key=lambda pair: version_key(pair[0]))
        held = {semantic for semantic, _ in ordered}
        for (before_version, before), (version, codelist) in pairwise(ordered):
            removed = sorted(set(before.codes.ids()) - set(codelist.codes.ids()))
            if not removed:
                continue
            yield _next_major_finding(before, before_version, codelist, version, removed, held)


def _next_major_finding(
    before: Codelist, before_version: str, codelist: Codelist, version: str, removed: list[str], held: set[str]
) -> Finding:
    urn, earlier = _urn(codelist), _urn(before)
    major = f"{int(before_version.split('.', 1)[0]) + 1}.0.0"
    shown = ", ".join(removed[:3]) + (f" and {len(removed) - 3} more" if len(removed) > 3 else "")
    if major in held:
        return Blocked(
            rule="fmr.next-major",
            urn=str(urn),
            reason=(
                f"{codelist.id} {version} removes {shown} from {before_version}, so FMR expects {major}, "
                f"which the message already holds; renumber the versions with sl.rewrite"
            ),
        )
    return Question(
        key=f"fmr.next-major@{urn}",
        rule="fmr.next-major",
        urn=str(urn),
        question=(
            f"{urn.agency}:{codelist.id} {version} removes {shown} from the final {before_version} before it, "
            f"and FMR refuses that short of the next major, {major}. What should it be?"
        ),
        options=(
            Option(
                id="major",
                consequence=f"moves to {major}; references follow",
                edits=(Move(old=urn, new=SdmxUrn[object](urn.package, urn.artefact_class, urn.agency, urn.id, major)),),
            ),
            Option(id="omit-earlier", consequence=f"{before_version} is left out", edits=(Omit(urn=earlier),)),
            Option(id="keep", consequence=f"stays {version}, and FMR refuses it", edits=()),
        ),
        recommended="major",
    )


def _related_dimensions(msg: StructureMessage) -> Iterator[Finding]:
    for dsd in msg.data_structures:
        order = {dimension.id: position for position, dimension in enumerate(dsd.dimensions)}
        for attribute in dsd.attributes:
            related = attribute.related_dimensions
            if not related:
                continue
            served = sorted(related, key=lambda d: order.get(d, len(order)))
            if served != list(related):
                yield Change(
                    rule="fmr.related-dimensions",
                    urn=str(_urn(dsd)),
                    path=f"attributes[{attribute.id}].related_dimensions",
                    before=", ".join(related),
                    after=", ".join(served),
                    reason="FMR serves an attribute's related dimensions in the DSD's order",
                    edits=(),
                )


def _monolingual(msg: StructureMessage) -> Iterator[Finding]:
    for msd in msg.metadata_structures:
        for attribute in _every_attribute(msd.attributes):
            representation = attribute.representation
            if isinstance(representation, Facet) and not representation.is_multilingual:
                yield Change(
                    rule="fmr.monolingual",
                    urn=str(_urn(msd)),
                    path=f"attributes[{attribute.id}].representation.is_multilingual",
                    before="False",
                    after="True",
                    reason=(
                        "FMR drops a metadata attribute's isMultiLingual='false' TextFormat, and 3.0 reads one "
                        "with none as multilingual"
                    ),
                    edits=(),
                )


def _every_attribute(attributes: Iterable[MetadataAttribute]) -> Iterator[MetadataAttribute]:
    """Each metadata attribute and, depth first, its children."""
    for attribute in attributes:
        yield attribute
        yield from _every_attribute(attribute.children)


def _id_case(msg: StructureMessage) -> Iterator[Finding]:
    by_folded: dict[tuple[str, str, str, str], list[SdmxUrn[object]]] = {}
    for artefact in msg.all_artefacts():
        urn = _urn(artefact)
        by_folded.setdefault((urn.artefact_class, urn.agency, urn.id.upper(), urn.version), []).append(urn)
    for (_cls, _agency, upper, _version), urns in by_folded.items():
        if len(urns) > 1:
            shown = " and ".join(f"{urn.agency}:{urn.id}({urn.version})" for urn in urns)
            yield Blocked(
                rule="fmr.id-case-clash",
                urn=str(urns[0]),
                reason=(
                    f"FMR upper-cases ids, so {shown} are one artefact there, {upper}: at a legacy version the "
                    "second submission replaces the first, and at a final semantic one FMR refuses it; give one "
                    "of them another id with sl.rewrite"
                ),
            )
            continue
        (urn,) = urns
        if urn.id == upper:
            continue
        renamed = SdmxUrn[object](urn.package, urn.artefact_class, urn.agency, upper, urn.version)
        yield Question(
            key=f"fmr.id-case@{urn}",
            rule="fmr.id-case",
            urn=str(urn),
            question=(
                f"FMR stores {urn.agency}:{urn.id} as {upper}, and SDMX ids are case-sensitive, so a reference to "
                f"{urn.id} stops resolving. Rename it to {upper}?"
            ),
            options=(
                Option(
                    id="rename",
                    consequence=f"renamed to {upper}; references follow",
                    edits=(Move(old=urn, new=renamed),),
                ),
                Option(
                    id="accept",
                    consequence=(
                        f"sent as {urn.id}; FMR stores it as {upper}, and references to {urn.id} stop resolving"
                    ),
                    edits=(),
                ),
            ),
            recommended="rename",
        )
