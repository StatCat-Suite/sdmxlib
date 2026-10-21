"""Regression tests for the v0.16.0 perf bugs (#53, #54).

These tests assert *behaviour*, not timing. The behaviours that caused
the regressions:

- ``slpl.label`` iterated ``codelist.codes`` building a ``Code`` object
  per row to construct an id→label dict. For CL_GEOGRAPHY (78k codes)
  that took 15.8s. The fix uses ``LazyCodelist.code_map(lang=)`` —
  direct SQL projection — and caches the result per codelist instance.
- ``CodeQuery.collect()`` always issued a second SQL round-trip to
  load every annotation and built ``Annotation`` per row, regardless
  of whether the caller wanted them. For 7716 codes that took 1.79s
  vs 10ms in raw SQL. The fix makes annotation hydration opt-in via
  ``.with_annotations(*types)``.

The tests inspect cache state and result shape rather than asserting
wall-clock times — perf-asserts are too flaky on shared CI.
"""

from datetime import UTC, datetime

import polars as pl
import pytest

import sdmxlib as sl
import sdmxlib.polars as slpl
from sdmxlib.local import LazyCodelist, LocalRegistry
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _large_codelist(n: int = 1000) -> Codelist:
    """Build a codelist with ``n`` codes for perf-shaped tests."""
    return Codelist(
        id="CL_LARGE",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Large codelist"),
        codes=[Code(id=f"C{i:05d}", name=InternationalString(en=f"Label {i}")) for i in range(n)],
    )


def _dsd_with_large_cl() -> DataStructure:
    cl_urn = Codelist.urn_for(agency="TEST", id="CL_LARGE", version="1.0")
    return DataStructure(
        id="DSD_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test DSD"),
        dimensions=[
            Dimension(id="LARGE", concept=concept_ref("LARGE"), representation=Ref[Codelist].unresolved(cl_urn)),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _annotated_codelist() -> Codelist:
    return Codelist(
        id="CL_ANN",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Annotated"),
        codes=[
            Code(
                id="A",
                name=InternationalString(en="A"),
                annotations=[Annotation(type="t", title="A title")],
            ),
            Code(
                id="B",
                name=InternationalString(en="B"),
                annotations=[Annotation(type="t", title="B title")],
            ),
        ],
    )


# ── #53 — slpl.label uses LazyCodelist.code_map fast-path + cache ──────


def test_label_populates_per_codelist_cache():
    """First slpl.label call against a codelist seeds the cache for that instance."""
    with LocalRegistry(":memory:") as local:
        local.add(_large_codelist(n=200))
        local.add(_dsd_with_large_cl())
        dsd = local.get(DataStructure.urn_for(agency="TEST", id="DSD_TEST", version="1.0"))
        assert dsd is not None
        representation = dsd.dimensions["LARGE"].representation
        assert isinstance(representation, Ref)
        cl = representation()
        # Cache lives on the LazyCodelist instance (#155), keyed by lang, so
        # the narrowing is to the lazy class rather than to `Codelist`.
        assert isinstance(cl, LazyCodelist)
        assert "en" not in cl._code_map_cache

        df = pl.DataFrame({"LARGE": ["C00000", "C00001"]}).lazy()
        result = slpl.label(df, dsd, lang="en").collect()
        assert "LARGE_label_en" in result.columns

        # The cache now has an entry for this language on this instance.
        assert "en" in cl._code_map_cache
        # The cached map has every code, regardless of frame size.
        assert len(cl._code_map_cache["en"]) == 200


def test_label_caches_per_lang():
    """Two languages produce two cache slots on the same codelist instance."""
    cl = Codelist(
        id="CL_BI",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Bilingual"),
        codes=[Code(id="A", name=InternationalString(en="Annual", fr="Annuel"))],
    )
    cl_urn = Codelist.urn_for(agency="TEST", id="CL_BI", version="1.0")
    dsd = DataStructure(
        id="DSD_BI",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Bi DSD"),
        dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].unresolved(cl_urn))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    with LocalRegistry(":memory:") as local:
        local.add(cl)
        local.add(dsd)

        resolved = local.get(DataStructure.urn_for(agency="TEST", id="DSD_BI", version="1.0"))
        assert resolved is not None
        representation = resolved.dimensions["FREQ"].representation
        assert isinstance(representation, Ref)
        rep = representation()
        assert isinstance(rep, LazyCodelist)

        df = pl.DataFrame({"FREQ": ["A"]}).lazy()
        en = slpl.label(df, resolved, lang="en").collect()
        fr = slpl.label(df, resolved, lang="fr").collect()
        assert en["FREQ_label_en"][0] == "Annual"
        assert fr["FREQ_label_fr"][0] == "Annuel"

        assert "en" in rep._code_map_cache
        assert "fr" in rep._code_map_cache


def test_code_map_single_flight_builds_once_under_concurrency(monkeypatch):
    """N concurrent first-time callers share one SQL build (#155 herd).

    The single-flight lock in ``LazyCodelist.code_map`` must collapse the
    thundering herd: five threads asking the same instance for the same
    lang at once run the projection SQL exactly once, and all receive the
    same cached dict.
    """
    import threading

    from sdmxlib.storage import _projections
    from sdmxlib.storage import lazy as lazy_mod

    with LocalRegistry(":memory:") as local:
        local.add(_large_codelist(n=200))
        lazy = local.codelist(Codelist.urn_for(agency="TEST", id="CL_LARGE", version="1.0"))
        assert lazy is not None

        calls = 0
        call_lock = threading.Lock()
        # LazyCodelist.code_map calls the `code_map` bound in the lazy module's
        # namespace, so the *patch* has to land there. The original is read
        # from where it is defined — `lazy` only re-imports it, and reading a
        # re-export is what `reportPrivateImportUsage` is for.
        orig = _projections.code_map

        def counting_code_map(conn, urn, *, lang):
            nonlocal calls
            with call_lock:
                calls += 1
            return orig(conn, urn, lang=lang)

        monkeypatch.setattr(lazy_mod, "code_map", counting_code_map)

        n = 5
        barrier = threading.Barrier(n)
        results: list[dict[str, str]] = []
        results_lock = threading.Lock()

        def call() -> None:
            barrier.wait()  # release all threads together to force the race
            mapping = lazy.code_map(lang="en")
            with results_lock:
                results.append(mapping)

        threads = [threading.Thread(target=call) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert calls == 1  # single-flight: one SQL build despite n callers
        assert len(results) == n
        assert all(len(r) == 200 for r in results)
        # Every caller got the identical cached dict instance.
        assert all(r is results[0] for r in results)


def test_label_works_with_in_memory_codelist_fallback():
    """In-memory Codelist (no code_map method) still labels via the fallback path."""
    cl = _large_codelist(n=10)
    cl_urn = Codelist.urn_for(agency="TEST", id="CL_LARGE", version="1.0")
    dsd = DataStructure(
        id="DSD_M",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Mem"),
        dimensions=[Dimension(id="LARGE", concept=concept_ref("LARGE"), representation=Ref[Codelist].of(cl_urn, cl))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    df = pl.DataFrame({"LARGE": ["C00000", "C00003"]}).lazy()
    out = slpl.label(df, dsd, lang="en").collect()
    assert out["LARGE_label_en"].to_list() == ["Label 0", "Label 3"]


# ── #54 — annotations, and how the answer changed in #306 ─────────────


def test_collect_returns_whole_codes_including_annotations():
    """The opt-in is gone, and the round-trip it was avoiding is gone with it.

    #54's fix was `.with_annotations(*types)`: `CodeQuery.collect()` issued a
    second SQL round-trip per call and built an `Annotation` per row whether or
    not the caller wanted them — 1.79 s for 7716 codes — so hydration became
    opt-in. The cost was a `Code` that was *not* a `Code`: `collect()` returned
    artefacts with their annotations silently dropped, which is the kind of
    half-populated object a caller cannot tell from a genuinely unannotated
    one.

    #306 removed the trade rather than the feature. `collect()` resolves keys
    in one batch through `storage.resolve`, annotations included, so there is
    one round-trip whether or not anything is annotated and nothing to opt
    into. That the batch really is one round-trip is asserted in
    `tests/unit/test_query_resolve_binds.py`, which counts them — the right
    place for it, since it is a claim about binds rather than about shape.
    """
    with LocalRegistry(":memory:") as local:
        local.add(_annotated_codelist())
        lazy = local.codelist(Codelist.urn_for(agency="TEST", id="CL_ANN", version="1.0"))
        assert lazy is not None
        codes = lazy.codes.collect()
        assert len(codes) == 2
        for c in codes:
            assert len(c.annotations) == 1

    # And it survives the clauses `with_annotations` had to be threaded
    # through by hand, because it is not a clause.
    with LocalRegistry(":memory:") as local:
        local.add(_annotated_codelist())
        lazy = local.codelist(Codelist.urn_for(agency="TEST", id="CL_ANN", version="1.0"))
        assert lazy is not None
        narrowed = lazy.codes.filter(sl.q.code.id == "A").limit(1).collect()
        assert [a.type for a in narrowed[0].annotations] == ["t"]


# ── End-to-end shape: catalog query labels without per-Code overhead ──


def test_federated_catalog_query_with_labels():
    """End-to-end: catalog.query(with_labels=True) goes through the fast path."""
    with LocalRegistry(":memory:", source_registry="local") as local:
        local.add(_large_codelist(n=500))
        local.add(_dsd_with_large_cl())
        local.add(
            Dataflow(
                id="DF_TEST",
                maintainer="TEST",
                version="1.0",
                name=InternationalString(en="Test"),
                structure=Ref[DataStructure].unresolved(
                    DataStructure.urn_for(agency="TEST", id="DSD_TEST", version="1.0"),
                ),
            ),
        )
        flow_urn = Dataflow.urn_for(agency="TEST", id="DF_TEST", version="1.0")
        local.add(
            sl.DataflowBinding(
                dataflow=Ref[Dataflow].unresolved(flow_urn),
                database_role="memory",
                table_location="data.observations",
                partition_layout=(),
                dimension_column_map={"LARGE": "LARGE"},
                measure_column_map={"OBS_VALUE": "OBS_VALUE"},
                attribute_column_map={},
                structural_version="TEST:DSD_TEST(1.0)",
                source_registry="local",
                materialized_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
                bound_at=datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
            ),
        )

        cur = local.connection.cursor()
        cur.execute("CREATE SCHEMA IF NOT EXISTS data")
        cur.execute("CREATE TABLE data.observations (LARGE TEXT, OBS_VALUE DOUBLE)")
        cur.execute("INSERT INTO data.observations VALUES ('C00000', 1.0)")
        cur.execute("INSERT INTO data.observations VALUES ('C00001', 2.0)")

        catalog = local
        df = catalog.obs(flow_urn).frame(labels="en")
        assert "LARGE_label_en" in df.columns
        assert set(df["LARGE_label_en"].to_list()) == {"Label 0", "Label 1"}
