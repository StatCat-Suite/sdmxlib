"""Query-DSL benchmarks — compile cost and execution cost (#267, #306).

Nothing in `tests/bench` touched the query DSLs before this, so the two largest
modules in `src/` had no performance baseline at all. The #267 refactor moved
their whole predicate and ref hierarchy onto a shared generic base, which is
exactly the kind of change that can cost Python time without changing a byte of
SQL — so the baseline had to exist first.

#306 replaced those modules with the path language, and these benchmarks were
repointed rather than retired: the *shapes* are what they measure — one
predicate, a chain, a correlated `EXISTS`, an annotation reach, `is_in` at
three widths — and every one of them still exists. The numbers below are from
the old compiler and are kept as the thing to compare against, not as a
baseline the new one has been checked to meet.

Two costs are measured separately because they regress for different reasons:

- **Compile** — building the predicate objects and emitting SQL, with no
  database involved. This is where a refactor shows up: an extra method call
  per predicate, a per-element hook in ``is_in``, or a runtime generic
  subscript in ``_field``.
- **Execute** — the same query end to end against DuckDB. This can only
  regress if the *emitted SQL* changes, which `test_emitted_sql_is_stable`
  in the equivalence harness checks directly and more cheaply than timing.

``is_in`` is benchmarked at three widths on purpose: it is the one operator
that does per-element work at construction, so a hook added there is O(n) in
the size of the set, and SDMX code sets are routinely in the thousands.

**A known, accepted compile regression lives here.** #275 made two structural
changes to the compile path: every scalar emitter now routes through one
`RowKind.column` instead of ten copied ``isinstance`` chains, and the ``FROM``
/ ``WHERE`` build was factored into a ``_where`` shared with the projection
compiler. Together they cost **about +20 % worst-case compile**, 2.06 us to
2.5 us on a single predicate.

Three rounds of tightening kept it there rather than letting it grow: a
`NamedTuple` instead of a frozen dataclass for `ColumnSql` (158 ns versus
81 ns to build), skipping the params splice when a column binds nothing, and
splitting ``_where`` from ``_scan`` so only the projection path pays for the
immutable record. A `functools.partial` for the per-DSL wrapper would have
saved another frame and was rejected: it erases the row-kind type parameter,
which is the check #267 exists to provide.

The reason it was accepted, and the number to judge a future change against:
compile is ~1 % of a query. The same single-predicate query *executes* in
~206 us, so +0.5 us of compile is **+0.25 % end to end**, and every
``test_execute_*`` here stayed at or below the ~1.8 % noise floor. Note also
that ``test_compile_single_predicate`` reads +22 % at one fixture size and
+18 % at the other *on identical code* — treat anything under ~5 pp on these
as drift. A compile regression is worth chasing only if it also moves an
execute benchmark, or if it is large against 206 us.
"""

from collections.abc import Generator

import pytest

from sdmxlib.local import LazyCodelist, LocalRegistry
from sdmxlib.model.ref import artefact_urn
from sdmxlib.query.accessors import q
from tests.bench.fixtures import make_codelist

pytestmark = pytest.mark.bench

_SIZES = [
    pytest.param(100, id="100"),
    pytest.param(10000, id="10k"),
]


@pytest.fixture(scope="session", params=_SIZES)
def codelist_registry(request: pytest.FixtureRequest) -> Generator[tuple[LocalRegistry, str, int]]:
    """A LocalRegistry holding one Codelist of N codes."""
    cl = make_codelist(f"CL_QBENCH_{request.param}", request.param)
    reg = LocalRegistry(":memory:").open()
    reg.add(cl)
    yield reg, str(artefact_urn(cl)), request.param
    reg.close()


@pytest.fixture
def lazy_codes(codelist_registry: tuple[LocalRegistry, str, int]) -> tuple[LazyCodelist, int]:
    reg, urn, n = codelist_registry
    lazy = reg.codelist(urn)
    assert lazy is not None
    return lazy, n


# ── Compile only: no database involved ───────────────────────────────────────


def test_compile_single_predicate(benchmark, lazy_codes: tuple[LazyCodelist, int]) -> None:
    """Bench: build one equality filter and emit its SQL."""
    lazy, _ = lazy_codes

    def run() -> tuple[str, list[object]]:
        return lazy.codes.filter(q.code.id == "C1").to_sql()

    benchmark(run)


def test_compile_predicate_chain(benchmark, lazy_codes: tuple[LazyCodelist, int]) -> None:
    """Bench: a realistic multi-clause filter — the shape a caller writes."""
    lazy, _ = lazy_codes

    def run() -> tuple[str, list[object]]:
        return (
            lazy.codes.filter(q.code.id.str.starts_with("C"))
            .filter(q.code.depth <= 2)
            .filter(q.code.name["en"].str.folded.contains("code"))
            .filter(q.code.parent.id.is_not_null())
            .order_by(q.code.id)
            .limit(50)
            .to_sql()
        )

    benchmark(run)


def test_compile_nested_any_subquery(benchmark, lazy_codes: tuple[LazyCodelist, int]) -> None:
    """Bench: a correlated EXISTS — the deepest emitter path."""
    lazy, _ = lazy_codes

    def run() -> tuple[str, list[object]]:
        return lazy.codes.filter(q.code.descendants.any(q.code.id == "C1")).to_sql()

    benchmark(run)


def test_compile_annotation_predicate(benchmark, lazy_codes: tuple[LazyCodelist, int]) -> None:
    """Bench: the annotation EXISTS, which reaches a second relation."""
    lazy, _ = lazy_codes

    def run() -> tuple[str, list[object]]:
        return lazy.codes.filter(
            q.code.annotations.any(q.annotation.type == "geo_level", q.annotation.title == "City")
        ).to_sql()

    benchmark(run)


@pytest.mark.parametrize("width", [1, 100, 5000])
def test_compile_is_in(benchmark, lazy_codes: tuple[LazyCodelist, int], width: int) -> None:
    """Bench: ``is_in`` at three widths — the only per-element construction path.

    A coercion hook or validation added per element is invisible at width 1 and
    O(n) at width 5000, which is an ordinary size for an SDMX code set.
    """
    lazy, _ = lazy_codes
    values = [f"C{i}" for i in range(width)]

    def run() -> tuple[str, list[object]]:
        return lazy.codes.filter(q.code.id.is_in(values)).to_sql()

    benchmark(run)


# ── Execute: the same queries against DuckDB ─────────────────────────────────


def test_execute_single_predicate(benchmark, lazy_codes: tuple[LazyCodelist, int]) -> None:
    """Bench: filter to one code and materialise ids."""
    lazy, _ = lazy_codes

    def run() -> list[str]:
        return lazy.codes.filter(q.code.id == "C1").ids()

    benchmark(run)


def test_execute_predicate_chain(benchmark, lazy_codes: tuple[LazyCodelist, int]) -> None:
    """Bench: the multi-clause filter, executed."""
    lazy, _ = lazy_codes

    def run() -> list[str]:
        return (
            lazy.codes.filter(q.code.id.str.starts_with("C"))
            .filter(q.code.depth <= 2)
            .filter(q.code.parent.id.is_null())
            .limit(50)
            .ids()
        )

    benchmark(run)


def test_execute_collect_full(benchmark, lazy_codes: tuple[LazyCodelist, int]) -> None:
    """Bench: hydrate every Code — row construction dominates here, not SQL."""
    lazy, _ = lazy_codes

    def run() -> int:
        return len(lazy.codes.collect())

    benchmark(run)
