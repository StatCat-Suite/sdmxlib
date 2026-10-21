"""Tests for FederatedRegistry — agency routing across multiple sources."""

import httpx
import pytest

import sdmxlib as sl
from sdmxlib.api.federated import FederatedRegistry
from sdmxlib.api.policy import RegistryUnavailable
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from sdmxlib.model.registry import RegistryReader
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _cl(*, agency: str, id: str = "CL_X", version: str = "1.0") -> Codelist:
    return Codelist(
        id=id,
        maintainer=agency,
        version=version,
        name=InternationalString(en=id),
        codes=[Code(id="A", name=InternationalString(en="Annual"))],
    )


# ── Construction + Protocol conformance ─────────────────────────────────


def test_federated_registry_satisfies_protocol():
    fed = FederatedRegistry(routing={"X": InMemoryRegistry()})
    assert isinstance(fed, RegistryReader)


def test_federated_registry_repr_lists_routes():
    fed = FederatedRegistry(routing={"ESTAT": InMemoryRegistry(), "SDMX": InMemoryRegistry()})
    assert "ESTAT" in repr(fed)
    assert "SDMX" in repr(fed)


def test_federated_registry_repr_marks_default():
    default = InMemoryRegistry()
    fed = FederatedRegistry(routing={"ESTAT": InMemoryRegistry()}, default=default)
    assert "default" in repr(fed)


def test_routing_property_is_independent_view():
    """Mutating the returned routing dict doesn't affect the federation."""
    a = InMemoryRegistry()
    fed = FederatedRegistry(routing={"X": a})
    fed.routing["Y"] = a  # caller-local mutation
    assert "Y" not in fed.routing  # internal state unchanged


# ── Agency routing ──────────────────────────────────────────────────────


def test_routes_by_agency_to_correct_source():
    estat = LocalRegistry(":memory:", source_registry="ESTAT").open()
    sdmx = LocalRegistry(":memory:", source_registry="SDMX").open()
    estat.add(_cl(agency="ESTAT", id="CL_E"))
    sdmx.add(_cl(agency="SDMX", id="CL_S"))

    fed = FederatedRegistry(routing={"ESTAT": estat, "SDMX": sdmx})

    e = fed.get(Codelist.urn_for(agency="ESTAT", id="CL_E", version="1.0"))
    s = fed.get(Codelist.urn_for(agency="SDMX", id="CL_S", version="1.0"))

    assert e is not None
    assert s is not None
    assert e.id == "CL_E"
    assert s.id == "CL_S"

    estat.close()
    sdmx.close()


def test_default_handles_unrouted_agency():
    default = InMemoryRegistry()
    default.add(_cl(agency="MY_ORG", id="CL_LOCAL"))

    fed = FederatedRegistry(routing={"ESTAT": InMemoryRegistry()}, default=default)

    result = fed.get(Codelist.urn_for(agency="MY_ORG", id="CL_LOCAL", version="1.0"))
    assert result is not None
    assert result.id == "CL_LOCAL"


def test_no_default_unrouted_agency_raises_keyerror():
    fed = FederatedRegistry(routing={"ESTAT": InMemoryRegistry()})
    with pytest.raises(KeyError, match="MY_ORG"):
        fed.get(Codelist.urn_for(agency="MY_ORG", id="CL_X", version="1.0"))


def test_get_returns_none_when_routed_source_has_no_artefact():
    estat = LocalRegistry(":memory:").open()
    fed = FederatedRegistry(routing={"ESTAT": estat})
    # Route succeeds but the artefact isn't in the source.
    assert fed.get(Codelist.urn_for(agency="ESTAT", id="MISSING", version="1.0")) is None
    estat.close()


def test_source_for_returns_dispatch_target():
    estat = InMemoryRegistry()
    fed = FederatedRegistry(routing={"ESTAT": estat})
    assert fed.source_for(Codelist.urn_for(agency="ESTAT", id="X", version="1.0")) is estat


def test_source_for_returns_none_when_unrouted_no_default():
    fed = FederatedRegistry(routing={"ESTAT": InMemoryRegistry()})
    assert fed.source_for(Codelist.urn_for(agency="UNKNOWN", id="X", version="1.0")) is None


# ── Membership (__contains__) ───────────────────────────────────────────


def test_contains_dispatches_to_routed_source():
    estat = LocalRegistry(":memory:", source_registry="ESTAT").open()
    estat.add(_cl(agency="ESTAT", id="CL_X"))
    fed = FederatedRegistry(routing={"ESTAT": estat})
    assert Codelist.urn_for(agency="ESTAT", id="CL_X", version="1.0") in fed
    assert Codelist.urn_for(agency="ESTAT", id="OTHER", version="1.0") not in fed
    estat.close()


def test_contains_false_for_unrouted_agency():
    fed = FederatedRegistry(routing={"ESTAT": InMemoryRegistry()})
    assert Codelist.urn_for(agency="UNKNOWN", id="X", version="1.0") not in fed


def test_contains_false_for_non_urn():
    fed = FederatedRegistry(routing={"ESTAT": InMemoryRegistry()})
    assert "not-a-urn" not in fed


# ── Failure policy ──────────────────────────────────────────────────────


class _FailingRegistry:
    """Stub LocalRegistry that raises on every get()."""

    def __init__(self, exc: Exception, *, id: str | None = None) -> None:
        self._exc = exc
        self.id = id

    def get(self, urn: object) -> None:  # signature matches Protocol
        raise self._exc

    def __contains__(self, urn: object) -> bool:
        return False


def test_an_unreachable_source_raises_and_names_itself():
    failing = _FailingRegistry(httpx.ConnectError("network down"), id="ESTAT")
    fed = FederatedRegistry(routing={"ESTAT": failing})
    with pytest.raises(RegistryUnavailable, match="ESTAT"):
        fed.get(Codelist.urn_for(agency="ESTAT", id="X", version="1.0"))


def test_the_refusal_names_what_was_tried():
    """§5.8 asks for both. In a federation "which source?" is the first
    question and "reading what?" is the second, and a bare
    ``ConnectError: connection refused`` answers neither."""
    fed = FederatedRegistry(routing={"ESTAT": _FailingRegistry(httpx.ConnectError("down"), id="ESTAT")})
    urn = Codelist.urn_for(agency="ESTAT", id="X", version="1.0")
    with pytest.raises(RegistryUnavailable) as caught:
        fed.get(urn)

    message = str(caught.value)
    assert "ESTAT" in message
    assert "CL" not in message or str(urn) in message
    assert caught.value.tried == str(urn)


def test_the_underlying_cause_survives():
    cause = httpx.ConnectError("network down")
    fed = FederatedRegistry(routing={"ESTAT": _FailingRegistry(cause, id="ESTAT")})
    with pytest.raises(RegistryUnavailable) as caught:
        fed.get(Codelist.urn_for(agency="ESTAT", id="X", version="1.0"))
    assert caught.value.cause is cause
    assert caught.value.__cause__ is cause, "and as a chained traceback"


def test_an_oserror_is_also_unreachable():
    fed = FederatedRegistry(routing={"ESTAT": _FailingRegistry(OSError("disk full"))})
    with pytest.raises(RegistryUnavailable):
        fed.get(Codelist.urn_for(agency="ESTAT", id="X", version="1.0"))


def test_a_dead_source_is_never_reported_as_absent():
    """The whole of §5.8, in one assertion.

    `FailurePolicy.RETURN_NONE` used to make this return `None`, and the
    cookbook described that as *"source down or artefact missing — same
    outcome to the caller"*. They are different facts and the caller's
    response differs: retry a dead source, fall back for a missing artefact.
    """
    fed = FederatedRegistry(routing={"ESTAT": _FailingRegistry(httpx.ConnectError("down"), id="ESTAT")})
    urn = Codelist.urn_for(agency="ESTAT", id="X", version="1.0")

    # Written as a bare try rather than `pytest.raises`, because the claim is
    # about what `get` does *not* do — an assertion inside a `raises` block
    # never runs, so it would pin nothing.
    try:
        result = fed.get(urn)
    except RegistryUnavailable:
        return
    pytest.fail(f"a dead source came back as {result!r} instead of raising")


def test_absent_still_returns_none():
    """The other side of the rule, so the fix is not "everything raises"."""
    empty = InMemoryRegistry()
    fed = FederatedRegistry(routing={"ESTAT": empty})
    assert fed.get(Codelist.urn_for(agency="ESTAT", id="MISSING", version="1.0")) is None


def test_a_nested_registrys_refusal_is_not_re_wrapped():
    """A `FederatedRegistry` inside another already named its source. Wrapping
    again would bury the inner id behind the outer one — the message would
    name the router rather than the registry that actually failed."""
    inner = FederatedRegistry(routing={"ESTAT": _FailingRegistry(httpx.ConnectError("down"), id="INNER")})
    outer = FederatedRegistry(routing={"ESTAT": inner})
    with pytest.raises(RegistryUnavailable, match="INNER") as caught:
        outer.get(Codelist.urn_for(agency="ESTAT", id="X", version="1.0"))
    assert caught.value.source_id == "INNER"


def test_a_programmer_error_is_not_an_unreachable_source():
    """Only network-ish errors mean "could not reach"; a `ValueError` from a
    source is a bug and must not be dressed up as a transport failure."""

    class _BoomRegistry:
        id = "ESTAT"

        def get(self, urn: object) -> None:
            err = "something else"
            raise ValueError(err)

        def __contains__(self, urn: object) -> bool:
            return False

    fed = FederatedRegistry(routing={"ESTAT": _BoomRegistry()})
    with pytest.raises(ValueError, match="something else"):
        fed.get(Codelist.urn_for(agency="ESTAT", id="X", version="1.0"))


# ── Composition ─────────────────────────────────────────────────────────


def test_federated_registry_can_be_a_source_in_another_federated_registry():
    """Composition — the dispatcher itself satisfies the Protocol."""
    inner_local = InMemoryRegistry()
    inner_local.add(_cl(agency="ESTAT", id="CL_INNER"))
    inner_fed = FederatedRegistry(routing={"ESTAT": inner_local})

    outer_default = InMemoryRegistry()
    outer = FederatedRegistry(routing={"ESTAT": inner_fed}, default=outer_default)

    result = outer.get(Codelist.urn_for(agency="ESTAT", id="CL_INNER", version="1.0"))
    assert result is not None
    assert result.id == "CL_INNER"


# ── Public exports ──────────────────────────────────────────────────────


def test_top_level_exports():
    assert sl.FederatedRegistry is FederatedRegistry
    assert sl.RegistryUnavailable is RegistryUnavailable


# ── resolve= / partial=, translated per source (#343) ───────────────────

_DSD = sl.DataStructure.urn_for(agency="ESTAT", id="DSD_X", version="1.0")
_LATEST_DSD = sl.DataStructure.urn_for(agency="ESTAT", id="DSD_X")

_TRIMMED = ["A"]
_UNTRIMMED = ["A", "Q", "M"]


def _wire(codes: list[str] | None) -> bytes:
    """A DSD whose one dimension enumerates `codes`, or the DSD alone.

    `codes=None` omits the codelist entirely — what a provider returns for
    ``references=none``. Without that the response carries the codelist
    whatever was asked, the session absorbs it, and the reference comes back
    resolved even from an unresolved `get`: the fixture would answer the
    question instead of the code under test.
    """
    code_xml = "".join(f'<str:Code id="{c}"><com:Name xml:lang="en">{c}</com:Name></str:Code>' for c in (codes or []))
    codelists = (
        ""
        if codes is None
        else (
            "<str:Codelists>"
            '<str:Codelist id="CL_FREQ" agencyID="ESTAT" version="1.0" isFinal="false">'
            '<com:Name xml:lang="en">freq</com:Name>'
            f"{code_xml}</str:Codelist></str:Codelists>"
        )
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"'
        ' xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"'
        ' xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
        "<mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>"
        "<mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id='S'/></mes:Header>"
        f"<mes:Structures>{codelists}"
        '<str:DataStructures><str:DataStructure id="DSD_X" agencyID="ESTAT" version="1.0" isFinal="false">'
        '<com:Name xml:lang="en">dsd</com:Name>'
        "<str:DataStructureComponents><str:DimensionList id='DimensionDescriptor'>"
        '<str:Dimension id="FREQ" position="1"><str:ConceptIdentity>'
        "<Ref package='conceptscheme' class='Concept' maintainableParentID='CS' "
        "maintainableParentVersion='1.0' agencyID='ESTAT' id='FREQ'/>"
        "</str:ConceptIdentity><str:LocalRepresentation><str:Enumeration>"
        "<Ref package='codelist' id='CL_FREQ' agencyID='ESTAT' version='1.0' class='Codelist'/>"
        "</str:Enumeration></str:LocalRepresentation></str:Dimension>"
        "</str:DimensionList></str:DataStructureComponents>"
        "</str:DataStructure></str:DataStructures>"
        "</mes:Structures></mes:Structure>"
    ).encode()


def _rest_serving_by_detail():
    """A provider that trims codes unless ``detail=full`` is asked for, and a
    log of what each request asked for. Both facts are what these tests are
    about: `resolve=` decides whether a second request happens at all, and
    `partial=` decides its `detail`."""
    asked: list[tuple[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        detail = request.url.params.get("detail", "")
        references = request.url.params.get("references", "none")
        asked.append((references, detail))
        codes = None if references == "none" else (_UNTRIMMED if detail == "full" else _TRIMMED)
        return httpx.Response(200, content=_wire(codes), headers={"content-type": "application/xml"})

    return _handler, asked


def test_resolve_reaches_the_routed_rest_source():
    """The issue's headline: `fed.get(urn, resolve=True)` used to be a
    `TypeError`, so a caller holding a federation had to know which registry
    owned ESTAT in order to ask for a resolved fetch.

    Asserted on the *reference*, not on the call succeeding — a `resolve=`
    that was accepted and then dropped would still return a DSD.
    """
    handler, _ = _rest_serving_by_detail()
    with sl.RestRegistry("https://x.test/s", id="ESTAT", transport=httpx.MockTransport(handler)) as estat:
        fed = FederatedRegistry(routing={"ESTAT": estat})
        shallow = fed.get(_DSD)
        assert shallow is not None
        assert isinstance(shallow.dimensions["FREQ"].representation, Ref)
        assert not shallow.dimensions["FREQ"].representation.resolved

        deep = fed.get(_DSD, resolve=True)
        assert deep is not None
        assert isinstance(deep.dimensions["FREQ"].representation, Ref)
        assert deep.dimensions["FREQ"].representation.resolved


def test_partial_false_reaches_the_wire_through_a_federation():
    """`partial=False` means "untrimmed", and being handed the trimmed
    codelist silently is the #338 defect. Checked by the *request* — that a
    `detail=full` went out — because an identity map fills the earlier
    object in, so comparing before against after measures nothing (see
    `test_rest_partial.test_a_fuller_fetch_upgrades_the_interned_codelist`).

    Pinned URNs go through the same path since #344 — `test_rest_partial`
    covers both there; this one is about the federation not losing the
    keyword on the way.
    """
    handler, asked = _rest_serving_by_detail()
    with sl.RestRegistry("https://x.test/s", id="ESTAT", transport=httpx.MockTransport(handler)) as estat:
        fed = FederatedRegistry(routing={"ESTAT": estat})
        fed.get(_LATEST_DSD, resolve=True)
        assert "full" not in [detail for _, detail in asked], "partial defaults to True"

        untrimmed = fed.get(_LATEST_DSD, resolve=True, partial=False)

    assert "full" in [detail for _, detail in asked], f"never asked for it: {asked}"
    assert untrimmed is not None
    assert isinstance(untrimmed.dimensions["FREQ"].representation, Ref)
    assert [c.id for c in untrimmed.dimensions["FREQ"].codelist.codes] == _UNTRIMMED


def test_resolve_is_dropped_for_a_local_source_rather_than_refused():
    """#340 refuses `resolve=` on `LocalRegistry` because a store has already
    paid for every hop. Through a federation the caller cannot know which
    source will answer, so refusing there would make one keyword's legality
    depend on routing. Dropped instead — and the result still satisfies the
    request, which is what makes dropping honest rather than a silent no-op.
    """
    codelist = _cl(agency="MY_ORG")
    dsd = sl.DataStructure(
        id="D",
        maintainer="MY_ORG",
        version="1.0",
        name=InternationalString(en="d"),
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=sl.Ref.to(codelist))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    with LocalRegistry(":memory:") as local:
        local.add_all([codelist, dsd])
        fed = FederatedRegistry(routing={}, default=local)

        for resolve in (False, True, [Codelist]):
            got = fed.get(dsd.urn, resolve=resolve)
            assert got is not None
            assert isinstance(got.dimensions["FREQ"].representation, Ref)
            assert got.dimensions["FREQ"].representation.resolved, (
                f"a local store resolves unconditionally, whatever resolve={resolve!r} asked"
            )


def test_partial_false_against_a_local_source_is_refused():
    """The asymmetry with `resolve`, and the reason for it: a store holds
    whatever was written to it, so it can neither honour "untrimmed" nor
    discover that it has not. Dropping it would answer an explicit question
    with the opposite of what was asked."""
    codelist = _cl(agency="MY_ORG")
    with LocalRegistry(":memory:") as local:
        local.add(codelist)
        fed = FederatedRegistry(routing={}, default=local)

        assert fed.get(codelist.urn, partial=True) is not None, "the default is not refused"

        with pytest.raises(TypeError) as caught:
            fed.get(codelist.urn, partial=False)

    message = str(caught.value)
    assert "partial=False" in message
    assert "source_for" in message, "the refusal names the way to ask the source that fetches"


def test_a_mixed_federation_refuses_only_the_route_that_cannot_answer():
    """The refusal is per-URN, not per-federation — which is the whole point
    of routing by agency. The same call is fine for ESTAT and refused for a
    locally-authored agency, and a federation that refused either always or
    never would be wrong half the time."""
    handler, _ = _rest_serving_by_detail()
    own = _cl(agency="MY_ORG")
    with (
        LocalRegistry(":memory:") as local,
        sl.RestRegistry("https://x.test/s", id="ESTAT", transport=httpx.MockTransport(handler)) as estat,
    ):
        local.add(own)
        fed = FederatedRegistry(routing={"ESTAT": estat}, default=local)

        assert fed.get(_DSD, resolve=True, partial=False) is not None

        with pytest.raises(TypeError):
            fed.get(own.urn, partial=False)


def test_a_nested_federation_forwards_both():
    """A `FederatedRegistry` is itself a source, and now takes both keywords —
    so the composition case must not lose them at the outer layer."""
    handler, asked = _rest_serving_by_detail()
    with sl.RestRegistry("https://x.test/s", id="ESTAT", transport=httpx.MockTransport(handler)) as estat:
        inner = FederatedRegistry(routing={"ESTAT": estat})
        outer = FederatedRegistry(routing={"ESTAT": inner})
        got = outer.get(_DSD, resolve=True, partial=False)

    assert got is not None
    assert "full" in [detail for _, detail in asked], f"the outer layer dropped them: {asked}"


def test_get_still_takes_identity_alone():
    """The keywords are optional, and the Protocol is `get(urn, /)`. A
    positional-only call must keep working or every existing caller breaks."""
    codelist = _cl(agency="MY_ORG")
    with LocalRegistry(":memory:") as local:
        local.add(codelist)
        fed = FederatedRegistry(routing={}, default=local)
        assert fed.get(codelist.urn) is not None
        assert isinstance(fed, RegistryReader)
