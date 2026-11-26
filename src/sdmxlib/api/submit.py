"""Submitting structures to a registry, and reading its verdict on each (GH-400).

A Fusion Metadata Registry takes structures as a POST to its secure REST
endpoint, with the action in an ``ACTION`` header, and answers with an SDMX
2.1 ``RegistryInterface`` holding one ``SubmissionResult`` per maintainable.
Three things about that answer shape this module, each seen loading a .Stat
space into FMR 12.5.0 (GH-399):

- **A submission is atomic, and FMR names only its first problem.** One
  undeclared agency refuses the whole message. So `submission_order` puts the
  message's artefacts in an order each can load alone in, and the caller can
  send them one at a time for a verdict on each.
- **A 200 with no SubmissionResult means unchanged.** Resubmitting identical
  content gets an empty ``SubmitStructureResponse``.
- **A 200 is not trusted.** FMR 12.4.2 reported success on a submission it had
  truncated; `SubmissionReport.verify` reads each accepted artefact back.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Literal

import httpx2
from attrs import frozen
from lxml import etree

from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import AgencyScheme
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.model.registry import SupportsGet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import _collect_refs
from sdmxlib.model.version import version_key

type SubmissionStatus = Literal["accepted", "unchanged", "refused"]


@frozen
class SubmissionResult:
    """The registry's verdict on one artefact.

    Attributes:
        urn: The artefact, as text.
        status: ``"accepted"``, ``"unchanged"`` (a 200 with no result for it)
            or ``"refused"``.
        reason: The registry's text for a refusal, or a warning on an
            accepted one; None otherwise.
    """

    urn: str
    status: SubmissionStatus
    reason: str | None = None


class SubmissionNotHeldError(RuntimeError):
    """A registry reported an artefact accepted, and does not serve it."""


@frozen
class SubmissionReport:
    """Every artefact a submission sent, with the registry's verdict on each.

    Attributes:
        results: One per artefact, in the order sent.
    """

    results: tuple[SubmissionResult, ...]

    @property
    def accepted(self) -> tuple[str, ...]:
        """The URNs the registry accepted."""
        return self._with("accepted")

    @property
    def unchanged(self) -> tuple[str, ...]:
        """The URNs the registry already held as sent."""
        return self._with("unchanged")

    @property
    def refused(self) -> tuple[SubmissionResult, ...]:
        """The refusals, each with the registry's reason."""
        return tuple(result for result in self.results if result.status == "refused")

    def _with(self, status: SubmissionStatus) -> tuple[str, ...]:
        return tuple(result.urn for result in self.results if result.status == status)

    def verify(self, registry: SupportsGet) -> None:
        """Read every accepted artefact back from *registry*.

        Raises:
            SubmissionNotHeldError: Naming each accepted URN the registry does
                not serve.
        """
        missing = [urn for urn in self.accepted if registry.get(SdmxUrn.parse(urn)) is None]
        if missing:
            msg = f"{len(missing)} artefact(s) reported accepted and not served: {', '.join(missing)}"
            raise SubmissionNotHeldError(msg)


def submit_url(endpoint: str) -> str:
    """FMR's submission URL for a public REST *endpoint*: ``/ws/public/`` becomes ``/ws/secure/``.

    Raises:
        ValueError: If *endpoint* has no ``/ws/public/`` to derive it from.
    """
    if "/ws/public/" not in endpoint:
        msg = (
            f"cannot derive a submission URL from {endpoint!r}, which has no /ws/public/; "
            "pass submit_url= to FmrRegistry"
        )
        raise ValueError(msg)
    return endpoint.replace("/ws/public/", "/ws/secure/", 1).rstrip("/") + "/"


def results_of(response: httpx2.Response, sent: Sequence[str]) -> tuple[SubmissionResult, ...]:
    """The verdict on each URN of *sent*, read from the registry's *response*.

    A ``RegistryInterface`` names a result per artefact; an artefact it does
    not name was unchanged when the status is a success. Any other body is
    an error that refuses everything sent, with its text.
    """
    if b"RegistryInterface" not in response.content[:600]:
        reason = _error_text(response)
        return tuple(SubmissionResult(urn=urn, status="refused", reason=reason) for urn in sent)
    report = etree.fromstring(response.content)
    named: dict[str, SubmissionResult] = {}
    for result in report.iter("{*}SubmissionResult"):
        urn = next((u.text or "" for u in result.iter("{*}URN")), "")
        status = next((s.get("status") for s in result.iter("{*}StatusMessage")), None)
        texts = [t.text for t in result.iter("{*}Text") if t.text]
        reason = texts[-1] if texts else None
        verdict: SubmissionStatus = "accepted" if status in {"Success", "Warning"} else "refused"
        kept = reason if verdict == "refused" or status == "Warning" else None
        named[urn] = SubmissionResult(urn=urn, status=verdict, reason=kept)
    fallback: SubmissionStatus = "unchanged" if response.is_success else "refused"
    return tuple(named.get(urn, SubmissionResult(urn=urn, status=fallback)) for urn in sent)


def _error_text(response: httpx2.Response) -> str:
    try:
        root = etree.fromstring(response.content)
    except etree.XMLSyntaxError:
        return f"HTTP {response.status_code}: {response.text[:500]}"
    texts = [t.text.strip() for t in root.iter("{*}Text") if t.text and t.text.strip()]
    return texts[0] if texts else f"HTTP {response.status_code}"


def submission_order(msg: StructureMessage) -> list[SupportsUrn]:
    """*msg*'s artefacts in an order each can be loaded alone in.

    An artefact comes after what it references in the message, and after the
    agency scheme that declares its maintainer (a sub-agency ``CA1.QOL`` needs
    ``CA1:AGENCIES``). Versions of one artefact go in ascending order, since a
    registry judges each against the one before it. A cycle is broken where
    it is met.
    """
    artefacts = sorted(msg.all_artefacts(), key=_sort_key)
    by_urn = {str(artefact_urn(a)): a for a in artefacts}
    declares = _agency_declarations(msg)
    ordered: list[SupportsUrn] = []
    state: dict[str, Literal["visiting", "done"]] = {}

    def visit(urn: str) -> None:
        if state.get(urn) is not None:
            return
        state[urn] = "visiting"
        for needed in _needs(by_urn[urn], by_urn, declares):
            visit(needed)
        state[urn] = "done"
        ordered.append(by_urn[urn])

    for artefact in artefacts:
        visit(str(artefact_urn(artefact)))
    return ordered


def _sort_key(artefact: SupportsUrn) -> tuple[str, str, str, str]:
    return (artefact.sdmx_class, artefact.agency_id, artefact.id, version_key(artefact.version))


def _agency_declarations(msg: StructureMessage) -> dict[str, str]:
    """Agency id, as a maintainer spells it, to the URN of the scheme declaring it."""
    declared: dict[str, str] = {}
    for scheme in msg.agency_schemes:
        prefix = "" if scheme.agency_id == "SDMX" else f"{scheme.agency_id}."
        for agency in _agencies(scheme):
            declared.setdefault(f"{prefix}{agency}", str(artefact_urn(scheme)))
    return declared


def _agencies(scheme: AgencyScheme) -> Iterator[str]:
    yield from (agency.id for agency in scheme.agencies)


def _needs(artefact: SupportsUrn, by_urn: Mapping[str, SupportsUrn], declares: Mapping[str, str]) -> Iterable[str]:
    """The URNs in the message *artefact* must follow."""
    own = str(artefact_urn(artefact))
    scheme = declares.get(artefact.agency_id)
    if scheme is not None and scheme != own:
        yield scheme
    for _, ref in _collect_refs(artefact, own):
        try:
            owner = str(ref.urn.owner())
        except ValueError:
            continue
        if owner in by_urn and owner != own:
            yield owner
    same = (artefact.sdmx_class, artefact.agency_id, artefact.id)
    for earlier in by_urn.values():
        if (earlier.sdmx_class, earlier.agency_id, earlier.id) == same and _older(earlier, artefact):
            yield str(artefact_urn(earlier))


def _older(one: SupportsUrn, other: SupportsUrn) -> bool:
    return version_key(one.version) < version_key(other.version)
