"""The agency rule of a migration: every sub-agency declared by its parent's scheme (GH-399).

A maintainer ``CA1.QOL`` is agency ``QOL`` declared in the agency scheme
``CA1`` maintains, ``CA1:AGENCIES(1.0)``. A registry resolves every
maintainer that way, and FMR refuses an artefact whose maintainer it cannot
resolve. .Stat NSI serves sub-agencies whose scheme it does not serve.

- **agency.scheme** (change): the message's artefacts are maintained by
  sub-agencies their parent's scheme does not declare, and each has a name in
  ``agency_names``. The scheme is added with them, or the message's scheme is
  extended. A new scheme's own name is ``agency_names["CA1:AGENCIES"]``.
- **agency.unnamed** (blocked): such a sub-agency, or a scheme the message
  lacks, has no name in ``agency_names``. The IM requires both names, and the
  plan invents none.

A top-level agency is the target registry's to declare in ``SDMX:AGENCIES``,
which a message rarely holds, so it is not checked here.

`omit_agencies` is a rule a caller adds (`Target(rules=...)`):

- **agency.omitted** (change): an artefact a named agency maintains, or one
  that references an artefact left out, is left out.
"""

from collections.abc import Iterator, Mapping

from sdmxlib.migration._types import Add, Blocked, Change, Omit, Rule
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import Agency, AgencyScheme
from sdmxlib.model.ref import artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import _collect_refs
from sdmxlib.model.version import late_binds

type Finding = Change | Blocked

#: The id and version every agency scheme has.
_SCHEME_ID = "AGENCIES"
_SCHEME_VERSION = "1.0"


def findings(msg: StructureMessage, agency_names: Mapping[str, InternationalString]) -> Iterator[Finding]:
    """A finding per agency scheme the message's sub-agencies need and it does not hold whole."""
    held = {scheme.agency_id: scheme for scheme in msg.agency_schemes if scheme.id == _SCHEME_ID}
    needed: dict[str, list[str]] = {}
    for maintainer in sorted({artefact.agency_id for artefact in msg.all_artefacts()}):
        parent, dot, child = maintainer.rpartition(".")
        if not dot:
            continue
        scheme = held.get(parent)
        if scheme is not None and child in scheme.agencies.ids():
            continue
        needed.setdefault(parent, []).append(maintainer)
    for parent, maintainers in needed.items():
        scheme_key = f"{parent}:{_SCHEME_ID}"
        unnamed = [maintainer for maintainer in maintainers if maintainer not in agency_names]
        if parent not in held and scheme_key not in agency_names:
            unnamed.append(scheme_key)
        urn = str(AgencyScheme.urn_for(agency=parent, id=_SCHEME_ID, version=_SCHEME_VERSION))
        if unnamed:
            yield Blocked(
                rule="agency.unnamed",
                urn=urn,
                reason=(
                    f"{scheme_key} must declare {', '.join(maintainers)}, and the IM requires a name for each "
                    f"agency and for a new scheme; agency_names= has none for {', '.join(unnamed)}"
                ),
            )
            continue
        agencies = [
            Agency(id=maintainer.rpartition(".")[2], name=agency_names[maintainer]) for maintainer in maintainers
        ]
        existing = held.get(parent)
        scheme = (
            existing.add(agencies)
            if existing is not None
            else AgencyScheme(
                id=_SCHEME_ID,
                maintainer=parent,
                version=_SCHEME_VERSION,
                name=agency_names[scheme_key],
                agencies=agencies,
            )
        )
        yield Change(
            rule="agency.scheme",
            urn=str(artefact_urn(scheme)),
            path="agencies",
            before=", ".join(existing.agencies.ids()) if existing is not None else None,
            after=", ".join(scheme.agencies.ids()),
            reason=f"{', '.join(maintainers)} maintain artefacts, which needs {parent}:{_SCHEME_ID} to declare them",
            edits=(Add(artefact=scheme),),
        )


#: The rule id of `omit_agencies`'s changes.
OMIT_RULE = "agency.omitted"


def omit_agencies(*agencies: str) -> Rule:
    """A rule that leaves out what *agencies* maintain, and what references it.

    For test and training agencies a registry should not receive. Each
    artefact one of *agencies* maintains is left out and, repeatedly until
    nothing changes, so is each artefact that references one left out, so the
    result never names an artefact it does not hold. Each is a `Change` of
    rule ``agency.omitted`` with an `Omit` edit.

    Args:
        agencies: The maintainers to leave out, such as ``"TEST"``. A
            sub-agency is named in full (``"CA1.TEST"``); naming a parent does
            not leave out its sub-agencies.

    Returns:
        The rule, for `Target(rules=...)`.
    """
    left_out = frozenset(agencies)

    def find(msg: StructureMessage) -> Iterator[Change]:
        artefacts = {str(artefact_urn(artefact)): artefact for artefact in msg.all_artefacts()}
        names = {urn: _references(artefact) for urn, artefact in artefacts.items()}
        why: dict[str, str] = {
            urn: f"maintained by {artefact.agency_id}, which the caller leaves out"
            for urn, artefact in artefacts.items()
            if artefact.agency_id in left_out
        }
        changed = True
        while changed:
            changed = False
            for urn, named in names.items():
                if urn in why:
                    continue
                hit = next((gone for ref in named for gone in why if _binds(ref, SdmxUrn.parse(gone))), None)
                if hit is not None:
                    why[urn] = f"it references {hit}, which is left out"
                    changed = True
        for urn in artefacts:
            if urn in why:
                yield Change(
                    rule=OMIT_RULE,
                    urn=urn,
                    path="",
                    before=None,
                    after=None,
                    reason=why[urn],
                    edits=(Omit(urn=SdmxUrn.parse(urn)),),
                )

    return Rule(find=find)


def _binds(reference: SdmxUrn[object], urn: SdmxUrn[object]) -> bool:
    """Whether a reference to the maintainable *reference* names the artefact at *urn*.

    A late-bound version (``1.0+.0``, ``latest``) names every version it
    may resolve to.
    """
    if (reference.artefact_class, reference.agency, reference.id) != (urn.artefact_class, urn.agency, urn.id):
        return False
    return reference.version == urn.version or reference.is_latest or late_binds(reference.version, urn.version)


def _references(artefact: object) -> list[SdmxUrn[object]]:
    """The maintainables *artefact* references, each as its own URN."""
    named: dict[str, SdmxUrn[object]] = {}
    for _path, ref in _collect_refs(artefact, ""):
        try:
            owner = ref.urn.owner()
        except ValueError:
            owner = ref.urn
        named[str(owner)] = owner
    return [named[key] for key in sorted(named)]
