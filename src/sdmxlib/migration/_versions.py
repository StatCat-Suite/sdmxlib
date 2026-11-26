"""The version rules of a 2.1 → 3.x migration (GH-399).

SDMX 2.1 versions are ``X.Y`` with a separate ``isFinal`` flag. SDMX 3.0
reads finality from the version itself: ``X.Y.Z`` is final, ``X.Y.Z-draft``
is not, and the flag is gone. So for each artefact a migration holds:

- **version.semantic** (change): a final ``X.Y`` becomes ``X.Y.0``.
- **version.non-final** (question): a non-final ``X.Y`` (``isFinal="false"``,
  or absent, its default) becomes ``X.Y.0-draft``, or ``X.Y.0`` if a person
  decides it is final, or is left out.
- **version.semantic-non-final** (question): an ``X.Y.Z`` that 2.1 declares
  ``isFinal="false"``, which 3.x would read as final, becomes
  ``X.Y.Z-draft``, or stays ``X.Y.Z`` and final, or is left out.
- **version.is-final-dropped** (change): the 2.1 flag is cleared, since no 3.x
  format carries it.

The model keeps the 2.1 flag (`legacy_is_final`) only where it disagrees
with the finality the version implies, so None on ``X.Y`` means non-final and
None on ``X.Y.Z`` means final.
- **version.2.1-only** (blocked): ``1.03`` or ``1.2.3.4`` have no 3.x form.
- **version.unit-scheme** (blocked): an organisation unit scheme at a version
  other than ``1.0``, which 3.x prohibits.

And for each artefact the message references without holding it:

- **version.external** (question): ``X.Y`` becomes ``X.Y.0``, ``X.Y.0-draft``,
  or is left as it is. The plan cannot see the flag, so it recommends none.

Organisation schemes are left alone: every format fixes the agency, data
provider, data consumer and metadata provider schemes at ``1.0``, and 3.x
writes none of them with a version.
"""

import re
from collections.abc import Iterator

from sdmxlib.migration._types import Blocked, Change, Move, Omit, Option, Question, SetField
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.model.rewrite import annotation_urns
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import _collect_refs
from sdmxlib.model.version import compliance

#: The organisation schemes, which SDMX 3.x writes with no version.
_UNVERSIONED = frozenset(
    {"AgencyScheme", "DataProviderScheme", "DataConsumerScheme", "MetadataProviderScheme", "OrganisationUnitScheme"}
)

#: SDMX-ML 3.0's `LegacyVersionNumberType`: one or two numeric parts.
_LEGACY = re.compile(r"^(0|[1-9]\d*)(\.(0|[1-9]\d*))?$")

type Finding = Change | Question | Blocked

#: Each rule here that asks, and the option ids its questions take, so a
#: policy can be checked before any message is planned.
QUESTIONS: dict[str, tuple[str, ...]] = {
    "version.non-final": ("draft", "final", "omit"),
    "version.semantic-non-final": ("draft", "final", "omit"),
    "version.external": ("final", "draft", "leave"),
}


def findings(msg: StructureMessage, *, annotation_refs: frozenset[str] = frozenset()) -> Iterator[Finding]:
    """Every version finding for *msg*, held artefacts first, then the ones it only references.

    An annotation of a type in *annotation_refs* whose title is a URN counts as
    a reference, so an artefact only an annotation names is asked about too.
    """
    held: set[str] = set()
    for artefact in msg.all_artefacts():
        urn = _urn_of(artefact)
        held.add(str(urn))
        yield from _held(artefact, urn)
    yield from _external(msg, held, annotation_refs)


def _held(artefact: SupportsUrn, urn: SdmxUrn[object]) -> Iterator[Finding]:
    version = urn.version
    if urn.artefact_class in _UNVERSIONED:
        if urn.artefact_class == "OrganisationUnitScheme" and version != "1.0":
            yield Blocked(
                rule="version.unit-scheme",
                urn=str(urn),
                reason=(
                    f"SDMX 3.x prohibits a version on an organisation unit scheme, so {version!r} has no 3.x form; "
                    "move its units to a scheme at 1.0"
                ),
            )
        return
    if compliance(version) == {"2.1"}:
        yield Blocked(
            rule="version.2.1-only",
            urn=str(urn),
            reason=(
                f"version {version!r} is valid only in SDMX 2.1; name its 3.x version with "
                "`add_all(..., rewrite_version=...)` or `sl.versions.strip_leading_zeros` first"
            ),
        )
        return
    # 14 of the 30 artefact classes carry the 2.1 flag; the rest read as None.
    flag: bool | None = getattr(artefact, "legacy_is_final", None)
    if flag is not None:
        yield Change(
            rule="version.is-final-dropped",
            urn=str(urn),
            path="legacy_is_final",
            before=str(flag),
            after=None,
            reason="SDMX 3.x has no isFinal; finality is read from the version",
            edits=(SetField(urn=urn, name="legacy_is_final", value=None),),
        )
    if not _LEGACY.match(version):
        if flag is False:
            yield _semantic_non_final(urn)
        return
    semantic = _semantic(version)
    if flag:
        yield Change(
            rule="version.semantic",
            urn=str(urn),
            path="version",
            before=version,
            after=semantic,
            reason=f"a final 2.1 artefact is final in 3.x at a semantic version, {semantic}",
            edits=(Move(old=urn, new=_at(urn, semantic)),),
        )
        return
    draft = f"{semantic}-draft"
    yield Question(
        key=f"version.non-final@{urn}",
        rule="version.non-final",
        urn=str(urn),
        question=f"{urn.agency}:{urn.id}({version}) is not final in 2.1. Which 3.x version does it move to?",
        options=(
            Option(
                id="draft",
                consequence=f"moves to {draft}, a draft as in 2.1; references follow",
                edits=(Move(old=urn, new=_at(urn, draft)),),
            ),
            Option(
                id="final",
                consequence=f"moves to {semantic}, final from now on; references follow",
                edits=(Move(old=urn, new=_at(urn, semantic)),),
            ),
            _omit(urn),
        ),
        recommended="draft",
    )


def _semantic_non_final(urn: SdmxUrn[object]) -> Question:
    draft = f"{urn.version}-draft"
    return Question(
        key=f"version.semantic-non-final@{urn}",
        rule="version.semantic-non-final",
        urn=str(urn),
        question=(
            f"{urn.agency}:{urn.id}({urn.version}) is isFinal=false in 2.1, and 3.x reads {urn.version} as final. "
            "Which 3.x version does it move to?"
        ),
        options=(
            Option(
                id="draft",
                consequence=f"moves to {draft}, a draft as in 2.1; references follow",
                edits=(Move(old=urn, new=_at(urn, draft)),),
            ),
            Option(id="final", consequence=f"stays {urn.version}, final from now on", edits=()),
            _omit(urn),
        ),
        recommended="draft",
    )


def _omit(urn: SdmxUrn[object]) -> Option:
    return Option(
        id="omit",
        consequence="left out of the result; references to it keep naming it at its 2.1 version",
        edits=(Omit(urn=urn),),
    )


def _external(msg: StructureMessage, held: set[str], annotation_refs: frozenset[str]) -> Iterator[Finding]:
    """One question per artefact *msg* references at a 2.1 version and does not hold."""
    asked: set[str] = set()
    for urn in _referenced(msg, annotation_refs):
        owner = _owner(urn)
        key = str(owner)
        if key in held or key in asked or owner.artefact_class in _UNVERSIONED:
            continue
        if urn.is_latest or not _LEGACY.match(owner.version):
            continue
        asked.add(key)
        semantic = _semantic(owner.version)
        yield Question(
            key=f"version.external@{owner}",
            rule="version.external",
            urn=key,
            question=(
                f"{owner.agency}:{owner.id}({owner.version}) is referenced and not in the message, so its "
                "isFinal is unknown. Which 3.x version do references to it name?"
            ),
            options=(
                Option(
                    id="final",
                    consequence=f"references name {semantic}",
                    edits=(Move(old=owner, new=_at(owner, semantic)),),
                ),
                Option(
                    id="draft",
                    consequence=f"references name {semantic}-draft",
                    edits=(Move(old=owner, new=_at(owner, f"{semantic}-draft")),),
                ),
                Option(
                    id="leave",
                    consequence=(f"references keep {owner.version}, which resolves only where that version is loaded"),
                    edits=(),
                ),
            ),
        )


def _referenced(msg: StructureMessage, annotation_refs: frozenset[str]) -> Iterator[SdmxUrn[object]]:
    """Every URN *msg*'s artefacts reference: their `Ref`s, then the annotation URNs *annotation_refs* names."""
    for artefact in msg.all_artefacts():
        label = f"{artefact.sdmx_class}:{artefact.agency_id}:{artefact.id}"
        for _, ref in _collect_refs(artefact, label):
            yield ref.urn
        yield from annotation_urns(artefact, annotation_refs)


def _semantic(version: str) -> str:
    """``X`` or ``X.Y`` as the three-part ``X.Y.0``."""
    parts = version.split(".")
    return ".".join([*parts, *["0"] * (3 - len(parts))])


def _at(urn: SdmxUrn[object], version: str) -> SdmxUrn[object]:
    return SdmxUrn[object](
        package=urn.package, artefact_class=urn.artefact_class, agency=urn.agency, id=urn.id, version=version
    )


def _urn_of(artefact: SupportsUrn) -> SdmxUrn[object]:
    return SdmxUrn.parse(str(artefact_urn(artefact)))


def _owner(urn: SdmxUrn[object]) -> SdmxUrn[object]:
    try:
        return urn.owner()
    except ValueError:
        return urn
