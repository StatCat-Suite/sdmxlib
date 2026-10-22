"""Observations over HTTP — the step that ties the data plane together.

`RestRegistry.obs` plans a query against what `/data` can answer,
fetches exactly that, materialises it, and then runs the **original** query
locally. The structural `query()` design, one plane over, and the thing worth
proving is the last clause: the stub below always returns every row, so any
narrowing in a result came from SQL after the fetch rather than from the URL.

Two seams are stubbed and no more — `_resolve_data_structure`, so the test does
not need a structural endpoint, and `get_data_at`, which is the whole HTTP
surface this feature uses. Everything between them is the real code.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.model.concept import Concept
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref

pytestmark = pytest.mark.unit

_CSV = b"""DATAFLOW,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE
ECB:EXR(1.0),M,USD,2021-01,1.2
ECB:EXR(1.0),M,USD,2021-02,1.3
ECB:EXR(1.0),M,GBP,2021-01,0.9
"""

_FLOW = sl.Dataflow.urn_for(agency="ECB", id="EXR", version="1.0")


def _concept(id_: str) -> Ref[Concept]:
    return Ref[Concept].unresolved(Concept.urn_for(agency="ECB", scheme="CS", id=id_, version="1.0"))


def _dsd() -> DataStructure:
    return DataStructure(
        id="EXR_DSD",
        maintainer="ECB",
        version="1.0",
        name={"en": "exchange rates"},
        dimensions=[
            sl.Dimension(id="FREQ", concept=_concept("FREQ")),
            sl.Dimension(id="CURRENCY", concept=_concept("CURRENCY")),
        ],
        time_dimension=sl.TimeDimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        # Typed, because `scan_csv` reads column types off the DSD: without a
        # facet the measure arrives as VARCHAR and a numeric filter is a binder
        # error rather than a wrong answer.
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"), representation=sl.Facet(type="Double"))],
    )


def _resolved(_urn: object) -> tuple[sl.Dataflow, DataStructure]:
    """What `_resolve_data_structure` returns: the dataflow at the version the
    provider resolved it to, which is ``1.0`` whatever was asked for."""
    dsd = _dsd()
    return sl.Dataflow(id="EXR", maintainer="ECB", version="1.0", name={"en": "f"}, structure=Ref.to(dsd)), dsd


@pytest.fixture
def reg(monkeypatch: pytest.MonkeyPatch):
    """A registry whose structure is local and whose `/data` calls are recorded.

    `requests` collects `(path, params)` per fetch, so a test can assert what
    went over the wire *and* what came back — the two halves that must agree.
    """
    requests: list[tuple[str, dict[str, str]]] = []
    with sl.RestRegistry("https://example.test/service", id="ECB") as registry:
        monkeypatch.setattr(registry, "_resolve_data_structure", _resolved)

        def _stream(path: str, params, *, to) -> None:
            """Stands in for the HTTP stream — writes the body to the file the
            fetch reads *lazily*, which is the shape the real client has."""
            requests.append((path, dict(params)))
            to.write_bytes(_CSV)

        monkeypatch.setattr(registry._client, "stream_data_at", _stream)
        registry.requests = requests  # type: ignore[attr-defined]
        yield registry


# ── what goes over the wire ───────────────────────────────────────────────


def test_a_key_dimension_filter_becomes_a_key_segment(reg) -> None:
    obs = reg.obs(_FLOW)
    obs.filter(obs.col("CURRENCY") == "USD").frame()
    path, params = reg.requests[-1]
    assert path == "data/ECB,EXR,1.0/.USD"
    assert params == {}


def test_a_time_bound_becomes_a_period_parameter(reg) -> None:
    """And the key stays `all` — a range cannot go in a slot."""
    obs = reg.obs(_FLOW)
    obs.filter(obs.col("TIME_PERIOD") >= "2021-02").frame()
    path, params = reg.requests[-1]
    assert path == "data/ECB,EXR,1.0/all"
    assert params == {"startPeriod": "2021-02"}


def test_a_filter_nothing_narrows_is_refused_before_any_request(reg) -> None:
    """A measure filter alone would fetch the whole flow to filter it here (#515).

    `/data/…/all` with every observation the provider holds, for a filter that
    could only be applied afterwards — the §5 rule, refused before the request
    rather than discovered from the bill.
    """
    obs = reg.obs(_FLOW)
    before = len(reg.requests)
    with pytest.raises(sl.PushdownError, match=r"OBS_VALUE.*download all of EXR.*FREQ, CURRENCY, TIME_PERIOD"):
        obs.filter(obs.col("OBS_VALUE") > 1.0).frame()
    assert len(reg.requests) == before, "refused before anything was sent"


@pytest.mark.parametrize(
    ("narrowing", "request_"),
    [
        pytest.param(("CURRENCY", "USD"), ("data/ECB,EXR,1.0/.USD", {}), id="a-key-slot"),
        pytest.param(("TIME_PERIOD", "2021-01"), ("data/ECB,EXR,1.0/all", {"startPeriod": "2021-01"}), id="a-period"),
    ],
)
def test_a_filter_that_cannot_push_runs_here_when_something_else_narrows(reg, narrowing, request_) -> None:
    """The discrimination check: an unpushable filter is fine beside a pushed one."""
    obs = reg.obs(_FLOW)
    column, value = narrowing
    narrowed = obs.col(column) == value if column == "CURRENCY" else obs.col(column) >= value
    frame = obs.filter(narrowed, obs.col("OBS_VALUE") > 1.0).frame()
    assert reg.requests[-1] == request_
    assert sorted(frame.get_column("OBS_VALUE").to_list()) == [1.2, 1.3], "the measure filter applied locally"


def test_an_unfiltered_query_asks_for_everything_and_gets_it(reg) -> None:
    """Not refused: nothing was filtered, so nothing is filtered after a whole-flow fetch."""
    assert reg.obs(_FLOW).frame().height == 3
    assert reg.requests[-1] == ("data/ECB,EXR,1.0/all", {})


def test_an_unpinned_dataflow_is_fetched_at_the_version_it_resolved_to(reg) -> None:
    """GH-51: the data URL named ``latest``, which BIS and OECD refuse with a
    400 (ECB and Eurostat let it pass). The dataflow was fetched to read its
    structure, so its version is known; the request names it."""
    latest = sl.Dataflow.urn_for(agency="ECB", id="EXR")
    assert reg.obs(latest).frame().height == 3
    assert reg.requests[-1] == ("data/ECB,EXR,1.0/all", {})
    assert reg.obs(latest).plan.root.__table__.endswith("sdmx_data.obs_ECB_EXR_1_0")


# ── what the query returns ────────────────────────────────────────────────


def test_the_original_query_runs_after_the_fetch(reg) -> None:
    """The load-bearing claim.

    The stub returns all three rows whatever the URL says. So a result of two
    rows can only have come from the compiler re-applying the predicate over
    the materialised table — which is what makes an unpushable filter correct
    rather than merely slow.
    """
    obs = reg.obs(_FLOW)
    frame = obs.filter(obs.col("CURRENCY") == "USD").frame()
    assert len(frame) == 2
    assert set(frame["CURRENCY"].to_list()) == {"USD"}


def test_a_predicate_that_pushed_is_still_applied_locally(reg) -> None:
    """Pushing is an optimisation, never a substitute.

    A provider that ignored the key — or one whose key means something subtly
    different — must not widen the answer, so the pushed predicate is applied
    again in SQL. The stub ignores the key, which is exactly that provider.
    """
    obs = reg.obs(_FLOW)
    assert len(obs.filter(obs.col("CURRENCY") == "GBP").frame()) == 1


def test_terminals_over_one_question_fetch_once(reg) -> None:
    """Keyed by meaning, so `count()` then `frame()` is one round trip."""
    obs = reg.obs(_FLOW).filter(obs_col := reg.obs(_FLOW).col("CURRENCY") == "USD")
    obs.count()
    obs.frame()
    assert len(reg.requests) == 1
    assert obs_col is not None


def test_a_different_question_refetches_and_replaces(reg) -> None:
    """One table per dataflow, holding one question's rows.

    Appending would be correct for filtering — the original query re-applies
    every predicate — and wrong for counting, since two overlapping fetches
    would land the same rows twice. So a second question overwrites, and the
    first must re-fetch rather than read the second's rows.
    """
    obs = reg.obs(_FLOW)
    first = obs.filter(obs.col("CURRENCY") == "USD")
    assert len(first.frame()) == 2
    assert len(obs.filter(obs.col("CURRENCY") == "GBP").frame()) == 1
    assert len(first.frame()) == 2, "re-fetched rather than reading the other question's rows"
    assert len(reg.requests) == 3


# ── the synthesised binding ───────────────────────────────────────────────


def test_the_binding_is_synthesised_from_the_dsd(reg) -> None:
    """Same artefact a producer would store, different provenance — and
    nothing downstream can tell them apart."""
    root = reg.obs(_FLOW).plan.root
    binding = root.binding
    assert binding.dimension_column_map == {"FREQ": "FREQ", "CURRENCY": "CURRENCY", "TIME_PERIOD": "TIME_PERIOD"}
    assert binding.structural_version == "ECB:EXR_DSD(1.0)", "which structure these columns mean"
    assert binding.source_registry == "ECB"


def test_the_table_is_named_for_the_dataflow(reg) -> None:
    """One table per dataflow, so a reader can tell which is which, and two
    dataflows never share one."""
    assert reg.obs(_FLOW).plan.root.__table__.endswith("sdmx_data.obs_ECB_EXR_1_0")


# ── what `explain()` discloses ────────────────────────────────────────────


def test_explain_says_a_remote_query_fetches_first(reg) -> None:
    """Without the line, the plan reads as a local table scan.

    A query served over HTTP compiles to an ordinary SELECT over the store it
    will be materialised into, so the request that fills that table is
    invisible in the SQL. That is the difference between reading rows that are
    already here and downloading them, and it belongs in the plan.
    """
    obs = reg.obs(_FLOW)
    plan = obs.filter(obs.col("CURRENCY") == "USD").explain()
    assert "fetches before running" in plan
    assert "SELECT" in plan, "and still shows the SQL"


def test_explain_runs_nothing(reg) -> None:
    obs = reg.obs(_FLOW)
    obs.filter(obs.col("CURRENCY") == "USD").explain()
    assert reg.requests == [], "explaining is not executing"


def test_a_local_query_says_no_such_thing() -> None:
    """The line must not appear where there is nothing to fetch, or it stops
    meaning anything."""
    from sdmxlib.local.registry import LocalRegistry

    with LocalRegistry(":memory:") as local:
        assert "fetches before running" not in local.query(sl.q.dataflow).explain()


# ── how the fetched rows are typed ────────────────────────────────────────


def test_a_code_with_leading_zeros_survives_the_fetch(reg, monkeypatch) -> None:
    """The reason component columns are forced to VARCHAR rather than inferred.

    `007` inferred as an integer becomes `7`, which still looks like a code,
    still joins, and is wrong everywhere. DuckDB's sniffer happens to be
    careful here — but a column of `0100`, `0200`, `0300` gives it no reason
    to be, so the type is stated rather than hoped for.
    """
    csv = (
        b"DATAFLOW,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE\n"
        b"ECB:EXR(1.0),M,0100,2021-01,1.2\n"
        b"ECB:EXR(1.0),M,0200,2021-02,1.3\n"
    )

    def _stream(path, params, *, to):
        reg.requests.append((path, dict(params)))
        to.write_bytes(csv)

    monkeypatch.setattr(reg._client, "stream_data_at", _stream)
    frame = reg.obs(_FLOW).frame()
    assert frame["CURRENCY"].to_list() == ["0100", "0200"], "codes, not integers"


def test_a_measure_keeps_its_declared_type(reg) -> None:
    """Numeric because the DSD's facet says so, which is what makes a numeric
    comparison a query rather than a binder error."""
    obs = reg.obs(_FLOW)
    # Beside a key filter: a measure filter alone is refused as unbounded (#515).
    assert obs.filter(obs.col("CURRENCY") == "USD", obs.col("OBS_VALUE") > 1.25).count() == 1
