"""Integration tests for RestRegistry against live SDMX REST endpoints.

Cassettes are recorded under tests/integration/cassettes/ and replayed on
subsequent runs — no network needed after the initial recording.

To re-record: pytest -m integration --record-mode=all
"""

import pytest

import sdmxlib as sl
from sdmxlib.model.ref import Ref
from tests.support.narrow import present

pytestmark = pytest.mark.integration

_ECB = sl.Provider.ECB


@pytest.mark.vcr
def test_registry_codelist():
    """reg.get(Codelist).resolve() returns a resolved Codelist."""
    with sl.RestRegistry(_ECB) as reg:
        cl = reg.get(sl.Codelist.urn_for(agency="ECB", id="CL_CURRENCY"), resolve=True)
    assert present(cl).id == "CL_CURRENCY"
    assert present(cl).agency_id == "ECB"
    assert len(present(cl).codes) >= 1


@pytest.mark.vcr
def test_registry_data_structure():
    """reg.get(DataStructure).resolve() returns a resolved DSD with enumerated dims."""
    with sl.RestRegistry(_ECB) as reg:
        dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)
    assert present(dsd).id == "ECB_EXR1"
    assert present(dsd).agency_id == "ECB"
    assert len(present(dsd).enumerated_dimensions) >= 1
    for dim in present(dsd).enumerated_dimensions:
        assert isinstance(dim.representation, Ref)


@pytest.mark.vcr
def test_registry_dataflow():
    """reg.get(Dataflow).resolve() returns a resolved Dataflow with structure ref."""
    with sl.RestRegistry(_ECB) as reg:
        df = reg.get(sl.Dataflow.urn_for(agency="ECB", id="EXR"), resolve=True)
    assert present(df).id == "EXR"
    assert present(df).structure is not None
    assert present(present(df).structure).resolved


@pytest.mark.vcr
def test_registry_fetch_urn():
    """RestRegistry.fetch returns artefact with unresolved refs by URN."""
    urn = sl.Codelist.urn_for(agency="ECB", id="CL_CURRENCY")
    with sl.RestRegistry(_ECB) as reg:
        cl = reg.get(urn)
    assert present(cl).id == "CL_CURRENCY"


@pytest.mark.vcr
def test_registry_resolve_urn():
    """RestRegistry.resolve returns artefact with resolved refs by URN."""
    urn = sl.Codelist.urn_for(agency="ECB", id="CL_CURRENCY")
    with sl.RestRegistry(_ECB) as reg:
        cl = reg.get(urn, resolve=True)
    assert present(cl).id == "CL_CURRENCY"
    assert len(present(cl).codes) >= 1


@pytest.mark.vcr
def test_registry_cache_hit():
    """A second fetch of the same *pinned* artefact is served from cache.

    Asked by pinned URN, not by ``latest``. Both calls used to say ``latest``
    and the second was served from an index that never re-asked where the
    pointer pointed (#338) — so this asserted a cache hit that was really a
    staleness bug. A pinned version is immutable, which is what makes the hit
    legitimate; `latest` now re-asks, and `tests/unit/test_rest_latest.py`
    covers that half.
    """
    with sl.RestRegistry(_ECB) as reg:
        dsd1 = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)
        pinned = sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1", version=present(dsd1).version)
        dsd2 = reg.get(pinned, resolve=True)
    assert dsd1 is dsd2


@pytest.mark.vcr
def test_registry_populates_domain_registry():
    """After resolve(), fetched artefacts appear in rest.registry."""
    with sl.RestRegistry(_ECB) as rest:
        cl = rest.get(sl.Codelist.urn_for(agency="ECB", id="CL_CURRENCY"), resolve=True)
    codelists = rest.registry.get_all(sl.Codelist)
    assert any(c.id == "CL_CURRENCY" for c in codelists)
    # The codelist in the domain registry is the same object
    urn = sl.Codelist.urn_for(agency="ECB", id="CL_CURRENCY", version=present(cl).version)
    assert rest.registry.get(urn) is cl


@pytest.mark.vcr
def test_registry_domain_registry_binds_the_refs():
    """DSD dimension Refs are bound to the registry after resolve() (#492): equal to its own, finding what it holds."""
    with sl.RestRegistry(_ECB) as rest:
        dsd = rest.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)
    reg = rest.registry
    for dim in present(dsd).enumerated_dimensions:
        # `enumerated_dimensions` yields exactly the dimensions whose
        # representation is a `Ref`, but it is a property so nothing narrows
        # through it — say it with `isinstance`, which does.
        assert isinstance(dim.representation, Ref)
        held = reg.ref(dim.representation.urn)
        assert dim.representation == held
        assert dim.representation.resolved
        assert dim.representation() is held()


# ── Resolution mode tests ─────────────────────────────────────────────────────


@pytest.mark.vcr
def test_get_fetch_leaves_refs_unresolved():
    """get().fetch() populates the DSD itself but does not follow codelist refs."""
    with sl.RestRegistry(_ECB) as reg:
        dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"))
    assert present(dsd).id == "ECB_EXR1"
    assert len(present(dsd).dimensions) >= 1
    # Refs are present but not resolved — no second HTTP request was made
    for dim in present(dsd).enumerated_dimensions:
        assert isinstance(dim.representation, Ref)
        assert not dim.representation.resolved


@pytest.mark.vcr
def test_resolve_partial_true_resolves_the_codelist():
    """`partial=True` (the default) resolves FREQ's codelist to something usable.

    Named for what it checks. It was
    `test_resolve_partial_true_trims_codelists`, claiming "constrained FREQ
    codelist has fewer codes than the master" — which ECB does not do, measured
    across every enumerated dimension of ECB_EXR1 (see
    `test_partial_false_fetches_the_untrimmed_scheme_from_ecb`), and which the
    assertion never checked either: `>= 1` holds for any answer at all.

    `referencepartial` *permits* a provider to return only the codes a
    structure uses. Whether it does is the provider's business, so a live test
    against one that does not cannot assert it.
    """
    with sl.RestRegistry(_ECB) as reg:
        dsd_partial = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)
    freq_dim = present(dsd_partial).dimensions["FREQ"]
    assert isinstance(freq_dim.representation, Ref)
    assert freq_dim.representation.resolved, "or labelling silently does nothing"
    assert len(freq_dim.codelist.codes) >= 4, "A, Q, M and friends"


@pytest.mark.vcr
def test_resolve_partial_false_returns_full_codelist():
    """resolve(partial=False) — FREQ codelist contains all codes, not just constrained ones."""
    with sl.RestRegistry(_ECB) as reg:
        dsd_full = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True, partial=False)
    freq_dim = present(dsd_full).dimensions["FREQ"]
    assert isinstance(freq_dim.representation, Ref)
    assert freq_dim.representation.resolved
    full_count = len(freq_dim.codelist.codes)
    # The full CL_FREQ codelist contains annual, quarterly, monthly, etc.
    assert full_count >= 4


@pytest.mark.vcr
def test_partial_false_fetches_the_untrimmed_scheme_from_ecb():
    """Both requests reach ECB, and its answers agree.

    This test has been wrong twice, in opposite directions, and the second
    correction is a finding about the provider rather than about the library.

    **It never issued the second request.** The `latest` cache answered
    `partial=False` from the `referencepartial` fetch, so `detail=full` never
    went out — the cassette held a single interaction — and the assertion
    compared an object with itself. #338 removed that cache and this cassette
    is re-recorded with both.

    **Then the obvious repair was also wrong.** Asserting `full > partial`
    fails against ECB, measured across every enumerated dimension of
    ECB_EXR1::

        dimension         partial     full
        FREQ                   10       10
        CURRENCY              369      369
        CURRENCY_DENOM        369      369
        EXR_TYPE               36       36
        EXR_SUFFIX              6        6

    ECB does not trim: `referencepartial` *permits* a provider to return only
    the codes a structure uses, and this one returns the whole codelist
    either way. So `partial=True` — the default — saves nothing here, and the
    difference the parameter names cannot be demonstrated against this
    provider at all. `tests/unit/test_rest_partial.py` exercises it against a
    provider that does trim.

    What is left to check live is what a live test is for: the parameter is
    accepted, both requests are made, and the two answers are consistent.
    The count is read *before* the second fetch because a fuller fetch
    upgrades the interned artefact in place — see
    `test_a_fuller_fetch_upgrades_the_interned_codelist`.
    """
    urn = sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1")
    with sl.RestRegistry(_ECB) as reg:
        dsd_partial = reg.get(urn, resolve=True)
        partial_count = len(present(dsd_partial).dimensions["FREQ"].codelist.codes)
        dsd_full = reg.get(urn, resolve=True, partial=False)
        full_count = len(present(dsd_full).dimensions["FREQ"].codelist.codes)

    assert partial_count > 0, "a resolved codelist, not an empty one"
    assert full_count >= partial_count, "untrimmed can only be a superset"
    assert dsd_full is not dsd_partial or full_count == partial_count


@pytest.mark.vcr
def test_the_rest_path_language_scans_a_real_service() -> None:
    """The gap #312 left, now recorded.

    Four tests here covered `reg.query(sl.Dataflow, agency=…)`. #312 retired
    that entry point and the replacement issues different URLs
    (`?detail=full&references=children` rather than `?detail=full`), so the
    old cassettes could not serve it and this stood skipped, documenting what
    was untested: response shapes, error statuses, and whether a real service
    honours `references=` the way the spec reads.

    It does. The unit coverage in `tests/unit/test_query_rest_executor.py` is
    a strong test of the *closure* against a `FakeRestService` that implements
    `references=` from the SDMX meaning — this is the same question asked of
    a service that implements it from its own reading.
    """
    with sl.RestRegistry(_ECB) as reg:
        ids = reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ECB").ids()

    assert len(ids) > 50, "ECB publishes ~100 dataflows; a handful would mean a truncated scan"
    assert "EXR" in ids


@pytest.mark.vcr
def test_a_local_predicate_runs_after_the_fetch_against_a_real_service() -> None:
    """Materialise-then-query, on the wire.

    `name contains` cannot be pushed — SDMX-REST has no parameter for it — so
    the fetch is unnarrowed and the predicate runs in SQL over the rows that
    came back. Against a stub this only proves the planner; against ECB it
    also proves the rows survive the round trip well enough to filter.
    """
    with sl.RestRegistry(_ECB) as reg:
        matched = (
            reg.query(sl.q.dataflow)
            .filter(sl.q.dataflow.agency == "ECB")
            .filter(sl.q.dataflow.name["en"].str.contains("xchange"))
            .ids()
        )

    assert "EXR" in matched
    assert len(matched) < 20, "a filter that matched everything would pass for the wrong reason"


@pytest.mark.vcr
def test_a_crossing_predicate_is_served_by_references_children() -> None:
    """The claim the skip named explicitly: does a real service honour
    `references=children`?

    `dsd.dimensions.any(...)` crosses from the artefact to its components,
    which the closure turns into `references=children` — and the components
    only exist locally if ECB actually sent them. A provider that ignored the
    parameter would return bare DSDs and this would find nothing, silently.
    """
    with sl.RestRegistry(_ECB) as reg:
        ids = (
            reg.query(sl.q.dsd)
            .filter(sl.q.dsd.agency == "ECB")
            .filter(sl.q.dsd.dimensions.any(sl.q.component.id == "CURRENCY"))
            .ids()
        )

    assert ids, "empty would mean the components never arrived"
    assert "ECB_EXR1" in ids
