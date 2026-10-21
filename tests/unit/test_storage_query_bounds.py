"""Regression bounds for SQL round-trips in the storage layer.

These tests guard the three perf fixes landed against issues #72, #73,
and #74:

- Item-bearing readers (Codelist, ConceptScheme, Hierarchy, …) used to
  call ``_read_annotations`` per item, so a single read of a 300-item
  scheme issued 300+ queries. The fix bulk-loads annotations once per
  artefact subtree.

- ``LocalRegistry._resolve_refs`` used to issue one SQL probe per Ref. A
  60k-node Hierarchy with every node referencing the same Codelist
  thus loaded that Codelist 60k times. The fix collects Refs across the
  graph, deduplicates by URN, and groups item URNs by parent scheme so
  the scheme is read once per call.

- The per-call memo from the #72 fix didn't persist between
  ``reg.get()`` calls. Iterating N MetadataSets that share one MSD
  re-loaded the MSD N times — 5x slower than v0.27.0 for a downstream
  service's 293-set listing. The fix adds a bounded LRU resolver cache
  on the LocalRegistry, populated across calls and invalidated by mutations.

We assert *bounded* query counts (small constants), not wall-clock
times, since the contract we care about is "round-trips do not grow
with item count, Ref fan-out, or number of read calls."
"""

from typing import Any

import duckdb
import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.local.registry import ITEM_FLOOR
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


_NAME = InternationalString(en="Test")


def _text(sql: duckdb.Statement | str) -> str:
    """The SQL text a call ran: a cached store read arrives parsed (GH-39)."""
    return sql.query if isinstance(sql, duckdb.Statement) else sql


class CountingCursor:
    """Connection proxy that counts every ``execute`` call.

    Wraps a DuckDB connection (or cursor) and counts ``execute`` calls
    made directly on it AND on every cursor it spawns via ``cursor()``.
    Read paths in the LocalRegistry now create per-call cursors via
    ``conn.cursor()`` (issue #75), so the proxy must follow those
    cursors to keep the round-trip counts meaningful.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.executes: list[str] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: duckdb.Statement | str, *args: Any, **kwargs: Any) -> Any:
        text = _text(sql)
        self.executes.append(text)
        self.calls.append((text, args))
        return self._inner.execute(sql, *args, **kwargs)

    def cursor(self) -> Any:
        return _SpawnedCountingCursor(self._inner.cursor(), self)

    @property
    def count(self) -> int:
        return len(self.executes)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _SpawnedCountingCursor:
    """A cursor spawned from a CountingCursor — counts into the same bucket."""

    def __init__(self, inner: Any, parent: CountingCursor) -> None:
        self._inner = inner
        self._parent = parent

    def execute(self, sql: duckdb.Statement | str, *args: Any, **kwargs: Any) -> Any:
        text = _text(sql)
        self._parent.executes.append(text)
        self._parent.calls.append((text, args))
        return self._inner.execute(sql, *args, **kwargs)

    def cursor(self) -> Any:
        # DuckDB cursors can spawn sibling cursors too; follow them.
        return _SpawnedCountingCursor(self._inner.cursor(), self._parent)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _instrument(reg: LocalRegistry) -> CountingCursor:
    """Wrap reg's connection with a counter and return it.

    Replaces both the long-lived cursor (used by write paths) and the
    underlying connection (used by read paths via ``conn.cursor()``)
    with proxies that funnel into a single counter, so tests asserting
    round-trip counts see every read regardless of which handle path
    the registry took. Drops the thread-local cursor cache (#80) so
    subsequent reads create fresh cursors via the instrumented proxy.
    """
    import threading

    counter = CountingCursor(reg._conn)
    reg._conn = counter  # type: ignore[assignment]
    reg._cursor = counter  # type: ignore[assignment]
    reg._thread_local = threading.local()
    return counter


# ── Reader bounds (#73) ─────────────────────────────────────────────────


def _annotated_concept(i: int) -> Concept:
    return Concept(
        id=f"C{i:04d}",
        name=InternationalString(en=f"Concept {i}"),
        annotations=Annotations([Annotation(id=f"A{i}", text=InternationalString(en=f"note {i}"))]),
    )


def test_concept_scheme_reader_is_bounded():
    """Reading a 200-concept scheme issues a small constant number of queries."""
    cs = ConceptScheme(
        id="CS_BIG",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        concepts=[_annotated_concept(i) for i in range(200)],
    )
    with LocalRegistry(":memory:") as local:
        local.add(cs)
        counter = _instrument(local)
        result = local.get(ConceptScheme.urn_for(agency="SDMX", id="CS_BIG", version="1.0"))
        assert result is not None
        # Each annotation should round-trip exactly. Sanity: pick a random one.
        assert result.concepts[42].annotations[0].text["en"] == "note 42"
        # Expect O(1) executes per reader call, not O(items). Allow generous
        # headroom — schema/spine/items/istring/annotations is well under 20.
        assert counter.count < 20, f"too many executes: {counter.count}"


def test_codelist_eager_reader_is_bounded():
    codes = [
        Code(
            id=f"C{i:04d}",
            name=InternationalString(en=f"Code {i}"),
            annotations=Annotations([Annotation(id=f"A{i}", text=InternationalString(en=f"n{i}"))]),
        )
        for i in range(200)
    ]
    cl = Codelist(
        id="CL_BIG",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        codes=codes,
    )
    with LocalRegistry(":memory:") as local:
        local.add(cl)
        counter = _instrument(local)
        urn = Codelist.urn_for(agency="SDMX", id="CL_BIG", version="1.0")
        result = local.get(urn, eager=True)
        assert result is not None
        assert result.codes[42].annotations[0].text["en"] == "n42"
        assert counter.count < 20, f"too many executes: {counter.count}"


def test_hierarchy_reader_is_bounded():
    """A 500-node Hierarchy reads in a bounded number of SQL round-trips."""
    nodes = [
        HierarchicalCode(
            id=f"N{i:04d}",
            code=code_urn("CL", f"N{i:04d}"),
            annotations=Annotations([Annotation(id=f"A{i}", text=InternationalString(en=f"note {i}"))]),
        )
        for i in range(500)
    ]
    root = HierarchicalCode(id="ROOT", code=code_urn("CL", "ROOT"), children=nodes)
    h = Hierarchy(
        id="H_BIG",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        tree=[root],
    )
    with LocalRegistry(":memory:") as local:
        local.add(h)
        counter = _instrument(local)
        # Direct eager Hierarchy read (bypass LazyHierarchy short-circuit).
        from sdmxlib.storage import readers

        h_urn = str(Hierarchy.urn_for(agency="SDMX", id="H_BIG", version="1.0"))
        result = readers.get_artefact(local._c, h_urn, eager=True)
        assert result is not None
        # Sanity-check that annotations land on the right node.
        target = next(c for c in result.tree[0].children if c.id == "N0042")
        assert target.annotations[0].text["en"] == "note 42"
        assert counter.count < 20, f"too many executes: {counter.count}"


# ── Resolver bounds (#72) ───────────────────────────────────────────────


def test_resolve_refs_loads_each_parent_scheme_once_for_dsd(dsd):
    """A DSD with N components referencing one ConceptScheme reads it once."""
    n_components = 30
    cs = ConceptScheme(
        id="CS_TEST",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        concepts=[Concept(id=f"C{i:04d}", name=_NAME) for i in range(n_components)],
    )
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_ONE", version="1.0")
    cl = Codelist(
        id="CL_ONE",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        codes=[Code(id="X", name=_NAME)],
    )
    dsd = DataStructure(
        id="DSD_WIDE",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        dimensions=[
            Dimension(
                id=f"C{i:04d}",
                concept=Ref[Concept].unresolved(Concept.urn_for(agency="SDMX", scheme="CS_TEST", id=f"C{i:04d}")),
                representation=Ref[Codelist].unresolved(cl_urn),
            )
            for i in range(n_components)
        ],
    )
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        local.add(cs)
        local.add(cl)

        counter = _instrument(local)
        result = local.get(DataStructure.urn_for(agency="SDMX", id="DSD_WIDE", version="1.0"))
        assert result is not None
        # All concept and codelist Refs should be resolved.
        for dim in result.dimensions:
            assert present(dim.concept).resolved
            assert isinstance(dim.representation, Ref)
            assert dim.representation.resolved

        # The CS_TEST scheme appears exactly once in the SQL trace
        # (loaded once for all 30 concept Refs), and CL_ONE once.
        cs_loads = sum("FROM concept WHERE scheme_urn = ?" in sql for sql in counter.executes)
        cl_loads = sum("FROM code WHERE codelist_urn = ?" in sql for sql in counter.executes)
        assert cs_loads == 1, f"ConceptScheme should be loaded once, not {cs_loads}x (per-component fan-out regression)"
        assert cl_loads <= 1, f"Codelist loaded {cl_loads}x — should be 0 (Lazy) or 1"


def test_resolve_refs_reads_the_codes_asked_for_not_the_whole_codelist():
    """N node → Code refs resolve in one batched read of *those* codes (#378).

    This asserted `cl_loads == 1` until #378 — "the Codelist is loaded once for
    all N refs, not N times", which was the #72 fix and the right bound at the
    time. It is now **zero**, because loading the scheme was itself the cost: a
    28-node hierarchy over `CL_GEOGRAPHY` read all 579,951 codes to resolve 28
    refs.

    The property the old assertion was reaching for — no per-ref fan-out — is
    kept and tightened. One read, not N; and it must be a read *of the URNs
    asked for*, not of the codelist, which is the part `cl_loads == 1` could
    not distinguish and which is the whole of #378.
    """
    n_nodes = 300
    cl = Codelist(
        id="CL_TARGET",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        codes=[Code(id=f"C{i:04d}", name=_NAME) for i in range(n_nodes)],
    )

    def _code_urn(code_id: str) -> SdmxUrn[Code]:
        return SdmxUrn.make(
            Code,
            package="codelist",
            artefact_class="Code",
            agency="SDMX",
            id="CL_TARGET",
            version="1.0",
            item_id=code_id,
        )

    nodes = [
        HierarchicalCode(
            id=f"N{i:04d}",
            code=Ref[Code].unresolved(_code_urn(f"C{i:04d}")),
        )
        for i in range(n_nodes)
    ]
    h = Hierarchy(
        id="H_REFS",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        # The root's code is in the same codelist, so the batch stays one read.
        tree=[HierarchicalCode(id="ROOT", code=Ref[Code].unresolved(_code_urn("ROOT")), children=nodes)],
    )
    with LocalRegistry(":memory:") as local:
        local.add(h)
        local.add(cl)

        # Force the eager read path so _resolve_refs traverses the tree, built
        # and resolved inside one memo's binding as `get` does (#492).
        from sdmxlib.local.registry import _CallScopedMemo, _FIFOCache, _MemoResolver
        from sdmxlib.model._binding import binding
        from sdmxlib.storage import readers

        memo = _CallScopedMemo(_FIFOCache(0))
        with binding(_MemoResolver(local, memo)):
            eager_h = readers.get_artefact(
                local._c,
                str(Hierarchy.urn_for(agency="SDMX", id="H_REFS", version="1.0")),
                eager=True,
            )
            assert eager_h is not None

            counter = _instrument(local)
            assert local._conn is not None
            local._resolve_refs(local._conn, eager_h, memo=memo)

        scheme_loads = sum("FROM code WHERE codelist_urn = ?" in sql for sql in counter.executes)
        assert scheme_loads == 0, f"the whole codelist was loaded {scheme_loads}x to resolve {n_nodes} item refs — #378"

        targeted = [sql for sql in counter.executes if "FROM sdmx.code WHERE code_urn" in sql]
        assert len(targeted) == 1, (
            f"expected one batched read of the {n_nodes} code URNs, got {len(targeted)} (per-ref fan-out regression)"
        )

        # Sanity: every node's code Ref is now resolved. Without this the two
        # assertions above are satisfied by a resolver that does nothing.
        leaves = list(eager_h.tree[0].children)
        assert all(node.code.resolved for node in leaves)
        assert {node.code().id for node in leaves} == {f"C{i:04d}" for i in range(n_nodes)}


def test_resolve_refs_loads_distinct_parent_schemes_once_each(dsd):
    """Refs into N distinct codelists trigger exactly N loads."""
    cls = [
        Codelist(
            id=f"CL_{i}",
            maintainer="SDMX",
            version="1.0",
            name=_NAME,
            codes=[Code(id="X", name=_NAME)],
        )
        for i in range(5)
    ]
    dsd = DataStructure(
        id="DSD_MULTI",
        maintainer="SDMX",
        version="1.0",
        name=_NAME,
        dimensions=[
            Dimension(
                id=f"D{i}",
                concept=concept_ref(f"D{i}"),
                representation=Ref[Codelist].unresolved(Codelist.urn_for(agency="SDMX", id=f"CL_{i}", version="1.0")),
            )
            for i in range(5)
        ],
    )
    with LocalRegistry(":memory:") as local:
        local.add(dsd)
        for cl in cls:
            local.add(cl)

        counter = _instrument(local)
        result = local.get(DataStructure.urn_for(agency="SDMX", id="DSD_MULTI", version="1.0"))
        assert result is not None
        for dim in result.dimensions:
            assert isinstance(dim.representation, Ref)
            assert dim.representation.resolved

        # Count how many times each distinct codelist URN was used as the
        # parameter on a spine probe — should be exactly one each.
        codelist_urns = {str(Codelist.urn_for(agency="SDMX", id=f"CL_{i}", version="1.0")) for i in range(5)}
        seen: list[str] = []
        for sql, args in counter.calls:
            if "FROM artefact WHERE urn = ?" not in sql:
                continue
            if not args or not args[0]:
                continue
            param = args[0][0]
            if param in codelist_urns:
                seen.append(param)
        per_url_counts = {urn: seen.count(urn) for urn in codelist_urns}
        # Each codelist URN should be probed by a bounded constant number
        # of spine SELECTs — one in get_artefact's type dispatch, one inside
        # make_lazy_codelist's metadata load. The key invariant: this does
        # not grow with the number of Refs pointing at the URN.
        for urn, n in per_url_counts.items():
            assert n <= 2, f"Codelist {urn} probed {n}x via spine — should be at most 2 (per-ref fan-out regression)"


# ── LocalRegistry-level resolver cache (#74) ─────────────────────────────────


def _make_msd(msd_id: str = "MSD_BIG") -> MetadataStructure:
    """Annotated MSD with several attributes — non-trivial to read."""
    return MetadataStructure(
        id=msd_id,
        maintainer="STATCAN",
        version="1.0",
        name=_NAME,
        attributes=[
            MetadataAttribute(
                id=f"ATTR_{i}",
                concept=concept_ref(f"ATTR_{i}"),
                representation=Facet(type="String"),
                annotations=Annotations(
                    [Annotation(id=f"A{i}", text=InternationalString(en=f"note {i}"))],
                ),
            )
            for i in range(20)
        ],
    )


def _make_flow(msd: MetadataStructure) -> Metadataflow:
    """A Metadataflow wrapping *msd* (id derived so sets over one MSD share it)."""
    return Metadataflow(id=f"MDF_{msd.id}", maintainer="STATCAN", version="1.0", name=_NAME, structure=msd)


def _make_metadata_set(set_id: str, flow: Metadataflow) -> MetadataSet:
    return MetadataSet(
        id=set_id,
        maintainer="STATCAN",
        version="1.0",
        name=_NAME,
        structure=flow,
        attributes=[ReportedAttribute(id="ATTR_0", value="x")],
    )


def _count_flow_reads(counter: CountingCursor) -> int:
    """Count reads that materialise a Metadataflow (the set's shared structure ref)."""
    return sum("FROM metadataflow" in sql and "urn = ?" in sql for sql in counter.executes)


def test_repeated_metadataset_reads_load_structure_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """50 MetadataSets sharing one Metadataflow should resolve it exactly once."""
    msd = _make_msd()
    flow = _make_flow(msd)
    sets = [_make_metadata_set(f"MS_{i:03d}", flow) for i in range(50)]
    with LocalRegistry(":memory:") as local:
        local.add_all([msd, flow])
        for s in sets:
            local.add(s)

        # Instrument AFTER population so we count only read queries.
        counter = _instrument(local)
        for s in sets:
            set_urn = MetadataSet.urn_for(agency="STATCAN", id=s.id, version="1.0")
            result = local.get(set_urn)
            assert result is not None
            assert result.structure.resolved

        flow_loads = _count_flow_reads(counter)
        assert flow_loads == 1, (
            f"Metadataflow should be loaded once across {len(sets)} MetadataSet reads, got {flow_loads}x "
            f"(#74 cross-call resolver-cache regression)"
        )

        info = local.resolve_cache_info()
        assert info["entries"] >= 1, "Structure URN should be cached after the first read"
        assert info["hits"] >= len(sets) - 1, f"Expected ~{len(sets) - 1} cache hits, got {info['hits']}"


def test_resolve_cache_invalidated_on_add() -> None:
    """`reg.add` must clear the cache so subsequent reads see fresh data."""
    msd = _make_msd()
    flow = _make_flow(msd)
    ms = _make_metadata_set("MS_001", flow)
    with LocalRegistry(":memory:") as local:
        local.add_all([msd, flow, ms])
        set_urn = MetadataSet.urn_for(agency="STATCAN", id="MS_001", version="1.0")
        local.get(set_urn)  # warm cache
        assert local.resolve_cache_info()["entries"] >= 1

        # Mutate: add an unrelated artefact. Cache should drop.
        local.add(_make_msd("MSD_OTHER"))
        assert local.resolve_cache_info()["entries"] == 0

        counter = _instrument(local)
        local.get(set_urn)
        assert _count_flow_reads(counter) == 1, "Structure must be re-read after cache invalidation"


def test_resolve_cache_invalidated_on_drop() -> None:
    msd = _make_msd()
    flow = _make_flow(msd)
    ms = _make_metadata_set("MS_001", flow)
    other_urn = MetadataStructure.urn_for(agency="STATCAN", id="MSD_OTHER", version="1.0")
    with LocalRegistry(":memory:") as local:
        local.add_all([msd, flow, _make_msd("MSD_OTHER"), ms])
        set_urn = MetadataSet.urn_for(agency="STATCAN", id="MS_001", version="1.0")
        local.get(set_urn)
        assert local.resolve_cache_info()["entries"] >= 1

        local.drop(other_urn)
        assert local.resolve_cache_info()["entries"] == 0


def test_resolve_cache_fifo_eviction() -> None:
    """A bound of two floor-weight entries must evict the oldest (#386).

    Stated as `2 * ITEM_FLOOR` rather than as `2`, because the bound is now in
    *items*: these structures hold no `ItemList`, so each weighs the floor, and
    a bound of literally 2 would refuse every one of them as over-weight.
    """
    msds = [_make_msd(f"MSD_{i}") for i in range(3)]
    flows = [_make_flow(m) for m in msds]
    sets = [_make_metadata_set(f"MS_{i}", flows[i]) for i in range(3)]
    with LocalRegistry(":memory:", resolve_cache_items=2 * ITEM_FLOOR) as local:
        local.add_all([*msds, *flows])
        for s in sets:
            local.add(s)

        for s in sets:
            local.get(MetadataSet.urn_for(agency="STATCAN", id=s.id, version="1.0"))

        info = local.resolve_cache_info()
        assert info["max_items"] == 2 * ITEM_FLOOR
        assert info["weight"] <= info["max_items"], f"weight cap violated: {info}"
        assert info["entries"] <= 2, f"two floor-weight entries is what that budget buys: {info}"
        assert info["refused"] == 0, "each of these fits on its own"


def test_resolve_cache_disabled_with_size_zero() -> None:
    """resolve_cache_items=0 must keep the cache empty and re-read every URN."""
    msd = _make_msd()
    flow = _make_flow(msd)
    sets = [_make_metadata_set(f"MS_{i:03d}", flow) for i in range(5)]
    with LocalRegistry(":memory:", resolve_cache_items=0) as local:
        local.add_all([msd, flow])
        for s in sets:
            local.add(s)

        counter = _instrument(local)
        for s in sets:
            local.get(MetadataSet.urn_for(agency="STATCAN", id=s.id, version="1.0"))

        flow_loads = _count_flow_reads(counter)
        assert flow_loads == len(sets), (
            f"With caching disabled, structure should be loaded N={len(sets)}x, got {flow_loads}"
        )
        info = local.resolve_cache_info()
        assert info["entries"] == 0
        assert info["weight"] == 0
        assert info["max_items"] == 0


def test_clear_resolve_cache_resets_state() -> None:
    """clear_resolve_cache() purges entries and resets hit/miss counters."""
    msd = _make_msd()
    flow = _make_flow(msd)
    sets = [_make_metadata_set(f"MS_{i:03d}", flow) for i in range(3)]
    with LocalRegistry(":memory:") as local:
        local.add_all([msd, flow])
        for s in sets:
            local.add(s)
        for s in sets:
            local.get(MetadataSet.urn_for(agency="STATCAN", id=s.id, version="1.0"))
        before = local.resolve_cache_info()
        assert before["entries"] >= 1
        assert before["weight"] >= 1
        assert before["hits"] >= 1

        local.clear_resolve_cache()
        after = local.resolve_cache_info()
        assert after == {
            "entries": 0,
            "weight": 0,
            "max_items": before["max_items"],
            "hits": 0,
            "misses": 0,
            "refused": 0,
        }


def test_the_parent_scheme_urn_is_derived_once_per_scheme() -> None:
    """Every item URN in one scheme has the same parent, so deriving it per
    item re-derives a constant — 499,138 times on the census bundle's largest
    hierarchy, each building and stringifying an `SdmxUrn`.

    Asserted as a **count** of constructions, per CLAUDE.md: the saving is work
    not done, which is identical on a busy machine and an idle one.
    """
    from sdmxlib.model import urn as urn_module
    from sdmxlib.model.urn import SdmxUrn

    codes = [Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(40)]
    codelist = Codelist(id="CL_MANY", maintainer="AG", version="1.0", name={"en": "cl"}, codes=codes)
    hierarchy_nodes = [
        HierarchicalCode(
            id=f"N{i}",
            code=Ref[Code].unresolved(
                Code.parse_urn(f"urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL_MANY(1.0).C{i}")
            ),
        )
        for i in range(40)
    ]
    hierarchy = Hierarchy(id="H_MANY", maintainer="AG", version="1.0", name={"en": "h"}, tree=hierarchy_nodes)

    with LocalRegistry(":memory:", resolve_cache_items=0) as local:
        local.add_all([codelist, hierarchy])
        # Built before the patch goes on: `urn_for` calls `make` too, and
        # counting it would charge this assertion for the test's own setup.
        hierarchy_urn = Hierarchy.urn_for(agency="AG", id="H_MANY", version="1.0")
        made = 0
        real_make = SdmxUrn.make

        def counting_make(*args: Any, **kwargs: Any) -> Any:
            nonlocal made
            made += 1
            return real_make(*args, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(urn_module.SdmxUrn, "make", counting_make)
            resolved = local.get(hierarchy_urn, eager=True)

    assert len(present(resolved).codes) == 40, "the read still returns every node"
    assert made <= 1, (
        f"the parent scheme URN was built {made} times for 40 items of one scheme — "
        f"it is keyed on everything but the item id, so one is the whole batch"
    )


# ── Refs in a plain container resolve too (#392) ──────────────────────────


def _metadata_set_with_targets() -> tuple[Codelist, MetadataStructure, Metadataflow, MetadataSet]:
    """A `MetadataSet` whose `targets` point at two codes of a real codelist."""
    codelist = Codelist(
        id="CL_T",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "t"},
        codes=[Code(id="A", name={"en": "a"}), Code(id="B", name={"en": "b"})],
    )
    msd = _make_msd()
    flow = _make_flow(msd)
    targets = [
        Ref[object].unresolved(SdmxUrn.parse(f"urn:sdmx:org.sdmx.infomodel.codelist.Code=STATCAN:CL_T(1.0).{i}"))
        for i in ("A", "B")
    ]
    ms = MetadataSet(
        id="MS_T",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "ms"},
        structure=Ref[object].unresolved(flow.urn),
        targets=targets,
    )
    return codelist, msd, flow, ms


def test_an_eager_read_resolves_refs_in_a_plain_list() -> None:
    """`MetadataSet.targets` is `list[Ref]`, and nothing resolved it (#392).

    Both walkers descended into `ItemList` and nothing else, so a `Ref` in a
    plain container matched neither the `Ref` arm nor the container one. On
    the census bundle an *eager* read returned `targets` with `resolved=False`
    beside a `structure` ref that had resolved — `eager=True` means resolve the
    refs, and it silently did not.
    """
    codelist, msd, flow, ms = _metadata_set_with_targets()
    with LocalRegistry(":memory:", resolve_cache_items=0) as local:
        local.add_all([codelist, msd, flow, ms])
        read = local.get(MetadataSet.urn_for(agency="STATCAN", id="MS_T", version="1.0"), eager=True)

        assert present(read).structure.resolved, "the direct Ref field, which always worked"
        assert [t.resolved for t in present(read).targets] == [True, True], (
            f"targets left unresolved by an eager read: {present(read).targets}"
        )
        resolved = [t() for t in present(read).targets]
        assert all(isinstance(r, Code) for r in resolved)
        assert [r.id for r in resolved if isinstance(r, Code)] == ["A", "B"], "and they resolve to the right codes"


def test_a_plain_read_resolves_them_too() -> None:
    """There is no `LazyMetadataSet`, so `get()` without `eager` reads the
    whole artefact and runs the resolver over it.

    Worth pinning rather than assuming: the first version of this test asserted
    the opposite, on the assumption that every artefact has a lazy form. The
    consequence is that the defect was on *both* read paths, not only the eager
    one the issue measured.
    """
    codelist, msd, flow, ms = _metadata_set_with_targets()
    with LocalRegistry(":memory:", resolve_cache_items=0) as local:
        local.add_all([codelist, msd, flow, ms])
        read = local.get(MetadataSet.urn_for(agency="STATCAN", id="MS_T", version="1.0"))
        assert type(read).__name__ == "MetadataSet", "not a lazy wrapper"
        assert [t.resolved for t in present(read).targets] == [True, True]


def test_only_the_fields_declared_as_ref_containers_take_that_path() -> None:
    """The branch is per class, not per element — which is what keeps the fix
    free on a large scheme.

    `Codelist.codes` is an `ItemList[Code]`, so it is descended into as before;
    579,951 codes pay no `isinstance(item, Ref)` they did not pay before. A new
    container-of-`Ref` field appearing on a model class shows up here.
    """
    from sdmxlib.model._refshape import ref_container_fields

    assert ref_container_fields(MetadataSet) == {"targets"}
    assert ref_container_fields(Codelist) == frozenset(), "codes are items, not refs"
    assert ref_container_fields(DataStructure) == frozenset()
    assert ref_container_fields(Metadataflow) == frozenset(), "its targets are tuple[str, ...] (#362)"


# ── an artefact the store has no kind for is refused, not dropped (#394) ──


def test_an_unstorable_artefact_is_refused_with_a_reason() -> None:
    """A bare "Unsupported artefact type: X" reads as *malformed*, and it is
    not: the reader parsed the artefact and the model holds it fine. What is
    missing is a storage kind (#395), which has a different fix.
    """
    from sdmxlib.model.constraint import AttachmentConstraint

    # An `AttachmentConstraint`, the only type still refused. This was a
    # `HierarchicalCodelist`, then a `StructureMap`, and both became storable
    # under #395 — the comment warning about that moved with it twice before
    # the type ran out. `test_storage_maps` now guards the table against the
    # drift directly, in both directions.
    unstorable = AttachmentConstraint(id="AC", maintainer="AG", version="1.0", name={"en": "ac"})
    with LocalRegistry(":memory:") as local, pytest.raises(TypeError) as raised:
        local.add(unstorable)

    message = str(raised.value)
    assert "AttachmentConstraint" in message, "names the type"
    assert "not persisted" in message, "says not-stored rather than not-valid"
    assert "#395" in message, "points at where that is decided"
    assert "registry=" in message, "and names a way to keep the artefact"


def test_it_is_refused_rather_than_silently_skipped() -> None:
    """The property #394 is really about.

    Before `all_artefacts()` was completed, ingesting a message dropped these
    two **silently** — a `HierarchicalCodelist` named "Geographic hierarchy"
    ceased to exist and nothing said so. Filtering them out of `add_all` would
    have restored exactly that, which is why the refusal stands.

    `formats/gaps.py` states the rule: *a refusal is visible, a drop is not.*
    """
    from sdmxlib.model.constraint import AttachmentConstraint
    from sdmxlib.model.message import StructureMessage

    codelist = Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"}, codes=[])
    unstorable = AttachmentConstraint(id="AC", maintainer="AG", version="1.0", name={"en": "ac"})
    msg = StructureMessage(codelists=[codelist], attachment_constraints=[unstorable])
    assert len(msg.all_artefacts()) == 2, "the message offers both"

    with LocalRegistry(":memory:") as local:
        with pytest.raises(TypeError):
            local.add_all(msg.all_artefacts())
        counted = local.connection.execute("SELECT count(*) FROM sdmx.artefact").fetchone()
        assert counted is not None
        written = counted[0]

    assert written == 0, (
        f"{written} artefact(s) landed before the refusal — `add_all` is one transaction, "
        f"so a caller who catches the TypeError must not be left with half a message"
    )


def test_a_structure_set_round_trips_with_both_ref_lists() -> None:
    """`StructureSet` carries two independent ref lists, and one table holds
    both — `artefact_ref.role` is what keeps them apart (#395).

    The test a single-list container would not have caught: write `maps` and
    `related_structures`, read them back separately.
    """
    from sdmxlib.model.mapping import StructureSet

    def _ref(kind: str, id_: str) -> Ref[object]:
        return Ref[object].unresolved(SdmxUrn.parse(f"urn:sdmx:org.sdmx.infomodel.{kind}=AG:{id_}(1.0)"))

    maps = (_ref("mapping.RepresentationMap", "RM_A"), _ref("mapping.RepresentationMap", "RM_B"))
    related = (_ref("codelist.Codelist", "CL_R"),)
    original = StructureSet(
        id="SS",
        maintainer="AG",
        version="1.0",
        name={"en": "Related maps"},
        maps=maps,
        related_structures=related,
    )

    with LocalRegistry(":memory:") as local:
        local.add(original)
        back = local.get(original.urn)

    assert [str(r.urn) for r in present(back).maps] == [str(r.urn) for r in maps], "maps, in order"
    assert [str(r.urn) for r in present(back).related_structures] == [str(r.urn) for r in related], "and not merged"
    assert present(back).name == original.name
