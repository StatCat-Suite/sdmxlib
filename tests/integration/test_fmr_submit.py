"""`FmrRegistry.submit` against FMR 12.5.0, replayed from cassettes (GH-400).

Recorded against a local FMR (`bench/fmr/README.md`, the ``fmr-12.5``
container on port 8081), never a public registry: submitting writes. Each test
first adds agency ``AG`` to FMR's own ``SDMX:AGENCIES``, so a test stands
alone.

To re-record against a fresh container:
``pytest tests/integration/test_fmr_submit.py --record-mode=all``.
"""

from collections.abc import Callable

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.api.submit import (
    SubmissionNotHeldError,
    SubmissionReport,
    SubmissionResult,
    submission_order,
    submit_url,
)
from sdmxlib.migration import Target
from tests.support.narrow import present

pytestmark = pytest.mark.integration

registry_rules = pytest.importorskip("bench.fmr.registry_rules")
_build_base: Callable[[], sl.StructureMessage] = registry_rules.base
_build_actual: Callable[[sl.StructureMessage], sl.StructureMessage] = registry_rules.actual

_ENDPOINT = "http://127.0.0.1:8081/ws/public/sdmxapi/rest"
_TARGET = Target.fmr("12.5.0")


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, object]:
    """The integration matchers, and no Authorization header in a cassette."""
    return {"match_on": ["method", "scheme", "host", "port", "path", "query"], "filter_headers": ["authorization"]}


def _fmr() -> sl.FmrRegistry:
    return sl.FmrRegistry(_ENDPOINT, auth=("root", "password"))


def _with_agency(fmr: sl.FmrRegistry) -> None:
    """FMR's SDMX:AGENCIES, with AG added, submitted back."""
    # A local first: pyrefly does not solve `present(fmr.get(...))` through the generic call.
    held = fmr.get(sl.AgencyScheme.urn_for(agency="SDMX", id="AGENCIES", version="1.0"))
    agencies = present(held)
    if "AG" not in agencies.agencies.ids():
        agencies = agencies.add(sl.Agency(id="AG", name={"en": "test agency"}))
    report = fmr.submit(sl.StructureMessage(agency_schemes=[agencies]), target=_TARGET)
    assert report.refused == ()


def _base() -> sl.StructureMessage:
    return _build_base()


@pytest.mark.vcr
def test_each_artefact_is_accepted_and_read_back():
    with _fmr() as fmr:
        _with_agency(fmr)
        report = fmr.submit(_base(), target=_TARGET)
        assert len(report.accepted) == 5
        assert report.refused == ()
        report.verify(fmr)


@pytest.mark.vcr
def test_resubmitting_what_fmr_holds_is_unchanged():
    with _fmr() as fmr:
        _with_agency(fmr)
        fmr.submit(_base(), target=_TARGET)
        again = fmr.submit(_base(), target=_TARGET)
    assert len(again.unchanged) == 5
    assert again.accepted == ()


@pytest.mark.vcr
def test_a_refusal_names_its_artefact_and_the_rest_load():
    base = _base()
    actual = _build_actual(base)
    with _fmr() as fmr:
        _with_agency(fmr)
        report = fmr.submit(attrs.evolve(base, constraints=actual.constraints), target=_TARGET)
    (refused,) = report.refused
    assert refused.urn.endswith("DataConstraint=AG:CC_ACTUAL(1.0.0)")
    assert refused.reason == "Submission of DataConstraints with Role specified as 'Actual' is not supported"
    assert len(report.accepted) + len(report.unchanged) == 5


@pytest.mark.vcr
def test_a_message_submission_refuses_everything_for_one_problem():
    """Discrimination: the same content as one message gets one verdict for all of it."""
    base = _base()
    actual = _build_actual(base)
    with _fmr() as fmr:
        _with_agency(fmr)
        report = fmr.submit(attrs.evolve(base, constraints=actual.constraints), target=_TARGET, granularity="message")
    assert len(report.results) == 6
    assert {result.status for result in report.results} <= {"refused", "unchanged"}
    assert any("Actual" in (result.reason or "") for result in report.refused)


# ── Offline ────────────────────────────────────────────────────────────────


def test_the_submission_url_is_derived_from_a_public_endpoint():
    assert submit_url(_ENDPOINT) == "http://127.0.0.1:8081/ws/secure/sdmxapi/rest/"
    with pytest.raises(ValueError, match="pass submit_url= to FmrRegistry"):
        submit_url("https://registry.sdmx.org/sdmx/v2")


def test_the_global_registry_cannot_be_submitted_to_without_a_url():
    with sl.FmrRegistry() as fmr, pytest.raises(ValueError, match="has no /ws/public/"):
        fmr.submit(_base())


def test_the_order_loads_what_is_referenced_and_the_declaring_scheme_first():
    base = _base()
    agencies = sl.AgencyScheme(id="AGENCIES", maintainer="SDMX", name={"en": "a"}).add(
        sl.Agency(id="AG", name={"en": "ag"})
    )
    later = attrs.evolve(base.codelists[0], version="2.0.0")
    msg = attrs.evolve(base, agency_schemes=[agencies], codelists=[later, base.codelists[0]])
    order = [f"{a.sdmx_class}:{a.id}({a.version})" for a in submission_order(msg)]
    assert order[0] == "AgencyScheme:AGENCIES(1.0)"
    assert order.index("Codelist:CL(1.0.0)") < order.index("Codelist:CL(2.0.0)")
    assert order.index("ConceptScheme:CS(1.0.0)") < order.index("DataStructure:DSD(1.0.0)")
    assert order.index("DataStructure:DSD(1.0.0)") < order.index("Dataflow:DF(1.0.0)")


def test_verify_names_what_the_registry_does_not_serve():
    kept = sl.Codelist(id="KEPT", maintainer="AG", version="1.0.0", name={"en": "k"})
    holds = sl.InMemoryRegistry()
    holds.add(kept)
    report = SubmissionReport(
        (
            SubmissionResult(urn=str(sl.artefact_urn(kept)), status="accepted"),
            SubmissionResult(urn="urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:GONE(1.0.0)", status="accepted"),
        )
    )
    with pytest.raises(
        SubmissionNotHeldError, match=r"^1 artefact\(s\) reported accepted and not served: .*GONE\(1\.0\.0\)$"
    ):
        report.verify(holds)
