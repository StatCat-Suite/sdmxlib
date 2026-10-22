"""`RestRegistry` under threads — the contract #336 asked to be stated (§5.15).

The defect was not a violated contract but an **unstated** one: the local
`Registry` documents its thread-safety and meets it, while `RestRegistry` said
nothing and met nothing. `docs/design/registry-and-catalog.md` §4.1 is a FastAPI
facade, so "nothing" was not a viable answer.

Three properties, each asserted as a **count of expensive calls** per
`docs/design/caching.md`'s checklist — never wall-clock:

1. one query store, not one per racing thread
2. one fetch per distinct question, however many threads ask
3. one dataflow's table serves one question at a time, without interleaving

The seams stubbed are the two HTTP entry points (`fetch_into`'s client work via
`_fetch_for`, and `stream_data_at`); everything between them is real, including
the DuckDB store the threads share.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest

import sdmxlib as sl
from sdmxlib.model.concept import Concept
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref

pytestmark = [pytest.mark.unit, pytest.mark.slow]

_TIMEOUT = 10.0
_THREADS = 8
_FLOW = sl.Dataflow.urn_for(agency="ECB", id="EXR", version="1.0")
_CSV = b"""DATAFLOW,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE
ECB:EXR(1.0),M,USD,2021-01,1.2
ECB:EXR(1.0),M,GBP,2021-01,0.9
"""


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
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"), representation=sl.Facet(type="Double"))],
    )


def _resolved(_urn: object) -> tuple[sl.Dataflow, DataStructure]:
    """What `_resolve_data_structure` returns: the dataflow at the version the
    provider resolved it to, which is ``1.0`` whatever was asked for."""
    dsd = _dsd()
    return sl.Dataflow(id="EXR", maintainer="ECB", version="1.0", name={"en": "f"}, structure=Ref.to(dsd)), dsd


def _race[T](work: Callable[[int], T], *, threads: int = _THREADS) -> list[T]:
    """Run `work` on `threads` threads that provably start together.

    The barrier is what makes this a race rather than a sequence: without it
    the first thread routinely finishes before the last one starts, and every
    assertion below would hold for the wrong reason.
    """
    start = threading.Barrier(threads, timeout=_TIMEOUT)

    def _go(index: int) -> T:
        start.wait()
        return work(index)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return [f.result(timeout=_TIMEOUT) for f in [pool.submit(_go, i) for i in range(threads)]]


# ── one query store ───────────────────────────────────────────────────────


def test_racing_threads_share_one_query_store() -> None:
    """Two threads used to build two `:memory:` stores.

    The loser's store was leaked — still open, holding whatever that thread
    had fetched, read by nobody. A query could then correctly report an empty
    result, which is the worst shape a concurrency bug can take.
    """
    with sl.RestRegistry("https://example.test/service", id="ECB") as reg:
        stores = _race(lambda _i: reg._query_store())
    assert len({id(store) for store in stores}) == 1


def test_a_supplied_store_is_never_replaced_under_a_race(tmp_path) -> None:
    """`store` belongs to the caller; the lock must not tempt the
    lazy path into running at all."""
    with sl.LocalRegistry(tmp_path / "store.duckdb") as local:
        with sl.RestRegistry("https://example.test/service", id="ECB", store=local) as reg:
            stores = _race(lambda _i: reg._query_store())
        assert all(store is local for store in stores)
        assert reg._query_store_owned is None


# ── one fetch per question ────────────────────────────────────────────────


def test_one_question_asked_by_many_threads_fetches_once(monkeypatch) -> None:
    """The single-flight claim, at the level that motivated it.

    Eight threads run the same query. A plain memo without a gate lets all
    eight through the `if key in …` check before any of them records, so the
    count is the assertion — not "it was cached", which would pass anyway once
    the first one finished.
    """
    fetches: list[object] = []
    lock = threading.Lock()

    def _fetch_into(_store, plan, _client, *, projection=None, record=None) -> tuple[()]:
        with lock:
            fetches.append(plan)
        return ()

    monkeypatch.setattr("sdmxlib.api.registry.fetch_into", _fetch_into)
    with sl.RestRegistry("https://example.test/service", id="ECB") as reg:
        _race(lambda _i: reg.query(sl.q.codelist).ids())
    assert len(fetches) == 1


def test_different_questions_each_fetch(monkeypatch) -> None:
    """The other half: a gate that deduped too eagerly would answer the second
    question from the first one's rows, silently."""
    fetches: list[object] = []
    lock = threading.Lock()

    def _fetch_into(_store, plan, _client, *, projection=None, record=None) -> tuple[()]:
        with lock:
            fetches.append(plan)
        return ()

    monkeypatch.setattr("sdmxlib.api.registry.fetch_into", _fetch_into)
    with sl.RestRegistry("https://example.test/service", id="ECB") as reg:
        # One callable per root: the roots pin different artefact types, so no
        # single list of them types as one `Cols[R, A]`.
        questions: list[Callable[[], list[str]]] = [
            lambda: reg.query(sl.q.codelist).ids(),
            lambda: reg.query(sl.q.dataflow).ids(),
            lambda: reg.query(sl.q.dsd).ids(),
            lambda: reg.query(sl.q.concept).ids(),
        ]
        _race(lambda i: questions[i % len(questions)](), threads=len(questions))
    assert len(fetches) == len(questions)


def test_the_fetch_cache_is_bounded(monkeypatch) -> None:
    """Unbounded growth was the half of #336 that held regardless of threads.

    With a cache of 2, the third distinct question evicts the first — so
    re-asking it fetches again. That re-fetch is the observable proof the
    entry went away, and the reason the work must stay idempotent.
    """
    fetches: list[object] = []

    def _fetch_into(_store, plan, _client, *, projection=None, record=None) -> tuple[()]:
        fetches.append(plan)
        return ()

    monkeypatch.setattr("sdmxlib.api.registry.fetch_into", _fetch_into)
    with sl.RestRegistry("https://example.test/service", id="ECB", fetch_cache_size=2) as reg:
        questions: list[Callable[[], list[str]]] = [
            lambda: reg.query(sl.q.codelist).ids(),
            lambda: reg.query(sl.q.dataflow).ids(),
            lambda: reg.query(sl.q.dsd).ids(),
            lambda: reg.query(sl.q.codelist).ids(),
        ]
        for ask in questions:
            ask()
    assert len(fetches) == 4, "the evicted question was asked again and re-fetched"
    assert len(reg._fetched) == 2


# ── one table, one question ───────────────────────────────────────────────


def test_one_dataflow_question_asked_by_many_threads_fetches_once(monkeypatch) -> None:
    """The data plane's counterpart. Here a duplicate is worse than waste:
    two threads ingesting into one table would leave it holding neither
    question's rows cleanly."""
    calls: list[str] = []
    lock = threading.Lock()

    with sl.RestRegistry("https://example.test/service", id="ECB") as reg:
        monkeypatch.setattr(reg, "_resolve_data_structure", _resolved)

        def _stream(path: str, params, *, to) -> None:
            with lock:
                calls.append(path)
            to.write_bytes(_CSV)

        monkeypatch.setattr(reg._client, "stream_data_at", _stream)

        def _ask(_i: int) -> int:
            obs = reg.obs(_FLOW)
            return len(obs.filter(obs.col("CURRENCY") == "USD").frame())

        results = _race(_ask)

    assert len(calls) == 1, "one fetch, however many threads asked"
    assert set(results) == {1}, "and every thread got the right answer"


def test_alternating_questions_on_one_dataflow_do_not_interleave(monkeypatch) -> None:
    """Two questions, eight threads, one table. Each fetch replaces the table,
    so the invariant is that no thread ever reads a half-written one: every
    answer must be right for the question that thread asked.
    """
    lock = threading.Lock()
    calls: list[str] = []

    with sl.RestRegistry("https://example.test/service", id="ECB") as reg:
        monkeypatch.setattr(reg, "_resolve_data_structure", _resolved)

        def _stream(path: str, params, *, to) -> None:
            with lock:
                calls.append(path)
            to.write_bytes(_CSV)

        monkeypatch.setattr(reg._client, "stream_data_at", _stream)

        def _ask(index: int) -> tuple[str, list[str]]:
            currency = "USD" if index % 2 else "GBP"
            obs = reg.obs(_FLOW)
            frame = obs.filter(obs.col("CURRENCY") == currency).frame()
            return currency, frame["CURRENCY"].to_list()

        results = _race(_ask)

    for asked, got in results:
        assert got == [asked], f"asked for {asked}, read {got} — a torn table"


# ── the shared connection, which #300 already ruled out ───────────────────


def test_the_catalog_probe_uses_a_per_thread_handle() -> None:
    """Found by the tests above, which failed on roughly one run in three.

    `_current_catalog` ran ``SELECT current_database()`` on the store's *base*
    connection. `DuckDBPyConnection` is not thread-safe — two threads sharing
    it overwrite each other's row buffer (#75) — so `fetchone()` returned
    `None` and the branch commented "DuckDB always answers this" raised. It
    was the last place in `src/` still executing on `.connection`, which #300
    had already ruled out for exactly this reason.

    Amplified with an inner loop, the way `test_concurrent_reads.py` does,
    and the count was **measured rather than guessed**: at one call per thread
    the regression was caught on roughly one run in three, and at 40 it was
    still two in five — a guard that lets the defect back in most of the time.
    At 500 it caught the reverted line on 6 runs out of 6, and the file still
    finishes in under a second.
    """
    repeats = 500
    with sl.RestRegistry("https://example.test/service", id="ECB") as reg:
        seen = _race(lambda _i: {reg._current_catalog() for _ in range(repeats)})
    assert seen == [{"memory"}] * _THREADS, "a None row, or another thread's answer"
