"""A caller-supplied set, registered instead of bound (#271).

The issue reported that a 5000-code `is_in` emits 15 KB of SQL and 5001 binds,
and framed the cost as statement size, plan quality and cache-key size.
Measured, it is none of those: it is **parameter binding in DuckDB's Python
client**, which marshals Python objects one at a time while `register` hands
over a pointer to an Arrow buffer.

**Re-measured twice, and the two disagreed by 25x — over pandas, not DuckDB.**
#271 measured ~48 us per bound value and a crossover at about four values. A
later re-measure on duckdb 1.5.3 found ~1.9 us and a crossover around 500-1000,
and concluded DuckDB had got faster. It had not: converting each bound value,
DuckDB checks whether it is `pandas.NA`/`NaT`, and with pandas absent that check
retries `import pandas` twice per value and never remembers the failure (#411).
The first measurement ran without pandas, the second with it importable. Median
of 7, 200k-row table, duckdb 1.5.5:

    N        bound, no pandas    bound, pandas    registered
    10            5.4 ms             4.1 ms          6.5 ms
    100          13.7 ms             4.3 ms          5.4 ms
    500          43.6 ms             5.0 ms          5.5 ms
    1000         85.6 ms             5.4 ms          5.5 ms
    5000          445 ms            10.0 ms          6.5 ms
    20000        1635 ms            26.6 ms         10.1 ms
    50000        4121 ms            59.1 ms         13.6 ms

What survives is the *shape*, which is what the feature rests on: binding is
linear in the number of values and registering is very nearly flat, so the gap
keeps widening. The crossover is about a hundred values without pandas — which
is sdmxlib's default, since pandas is not a dependency — and about a thousand
with it.

`duckdb/duckdb-python#238`, cited here as upstream calling the binding cost
`wontfix`, is about something else: parameterised statements taking a
materialising code path. Re-measure before quoting any number here, and record
whether pandas was importable when you do — these are properties of a
dependency *and its environment*, not of this library.

## Why the caller owns the window

A registered relation is session state, and three things go wrong if its
lifetime is implicit: it outlives the query, a fixed name lets two callers
clobber each other, and a lazily-built query separates "register" from
"execute" with no safe moment to free in between. A block removes all three —
generated name, relation exists only inside, ``finally`` frees it on error too.

It also buys the thing an automatic threshold cannot: inside the block
`to_sql()` is self-contained, so a compiled query naming the relation can be
handed to another library and executed. Outside, the values would be gone
before the SQL was.

## No wall-clock test

There was one — `test_the_registered_path_is_dramatically_cheaper`, asserting
a 5x gap at 200 values. It failed once the per-value cost fell, correctly: the
measured gap at 200 is about 1.2x. It is deleted rather than retuned, because
`docs/design/caching.md` asks a guard to assert the **count** of expensive
calls rather than wall-clock, and two tests here already do exactly that —
`test_the_values_never_reach_the_parameter_list` and
`test_bind_count_does_not_grow_with_the_set` pin the mechanism (one bind
instead of N) in a way that cannot go stale when a dependency gets faster.
"""

import duckdb
import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.query.accessors import q

pytestmark = pytest.mark.unit

_N = 200


@pytest.fixture
def reg():
    with LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "Codes"},
                codes=[Code(id=f"C{i:04d}", name={"en": f"n{i}"}) for i in range(_N)],
            )
        )
        yield registry


@pytest.fixture
def codes(reg):
    return reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0")).codes


# ── It is a relation, so the existing seam takes it ──────────────────────────


def test_a_value_set_filters_the_same_rows_as_an_eager_list(reg, codes) -> None:
    """The differential claim: same answer, different mechanism.

    Asserted against the eager path rather than a literal expectation, so the
    two cannot drift — the registered path is an optimisation and must stay
    observationally identical.
    """
    wanted = [f"C{i:04d}" for i in range(0, 40, 3)]
    eager = codes.filter(q.code.id.is_in(wanted)).ids()
    with reg.session() as active, active.value_set(wanted) as registered:
        assert codes.filter(q.code.id.is_in(registered)).ids() == eager


def test_it_needs_no_change_to_is_in(reg, codes) -> None:
    """`is_in` has accepted a `SqlRelation` since #261.

    What was missing was a way to make one from a plain list — so this feature
    is a constructor, not a new code path through the DSL. Worth pinning: a
    future change that special-cased value sets inside `is_in` would be
    reintroducing the branch this avoided.
    """
    from sdmxlib.sql import SqlRelation

    with reg.session() as active, active.value_set(["C0001"]) as registered:
        assert isinstance(registered, SqlRelation)
        assert registered.columns == ("value",)


def test_the_values_never_reach_the_parameter_list(reg, codes) -> None:
    """The whole point, stated as a bind count.

    The eager path binds every element — as one list parameter since #411,
    one placeholder each before it — and the registered path binds none of
    them. This is the assertion that would fail if the implementation quietly
    fell back to binding.
    """
    wanted = [f"C{i:04d}" for i in range(50)]
    _, eager_params = codes.filter(q.code.id.is_in(wanted)).to_sql()
    assert eager_params[1:] == [wanted], "the codelist scope, then the set as one list"

    with reg.session() as active, active.value_set(wanted) as registered:
        _, params = codes.filter(q.code.id.is_in(registered)).to_sql()
    assert len(params) == 1, "only the codelist scope should remain"
    assert not any(isinstance(p, list) for p in params)


def test_bind_count_does_not_grow_with_the_set(reg, codes) -> None:
    """Bounded and independent of size — the issue's test gate."""
    counts = set()
    with reg.session() as active:
        for size in (1, 10, 100, _N):
            with active.value_set([f"C{i:04d}" for i in range(size)]) as registered:
                _, params = codes.filter(q.code.id.is_in(registered)).to_sql()
                counts.add(len(params))
    assert counts == {1}


# ── The case a threshold could never serve ───────────────────────────────────


def test_inside_the_block_to_sql_is_self_contained(reg, codes) -> None:
    """A third party can execute our compiled SQL.

    An automatic threshold cannot offer this: it would register at execution,
    so a caller handed the SQL would receive a reference to a relation that
    does not exist. Here the caller holds the block open, so the relation is
    there when they run it. This is why the explicit primitive exists rather
    than only a size heuristic.
    """
    wanted = [f"C{i:04d}" for i in range(20)]
    with reg.session() as active, active.value_set(wanted) as registered:
        sql, params = codes.filter(q.code.id.is_in(registered)).to_sql()
        # Standing in for statcatlib: a third party holding only our compiled
        # string and binds, running them on the connection it shares with us.
        rows = active.execute(sql, params).fetchall()
    assert len(rows) == len(wanted)


# ── Lifetime ─────────────────────────────────────────────────────────────────


def test_the_relation_is_gone_after_the_block(reg, codes) -> None:
    """No leak. A registered relation is connection state and would otherwise
    live for the thread's lifetime.
    """
    with reg.session() as active:
        with active.value_set(["C0001"]) as registered:
            # Through the relation's own `to_sql`, so the test interpolates
            # nothing and exercises the real accessor.
            inside_sql, inside_params = registered.to_sql()
            assert active.execute(inside_sql, inside_params).fetchall() == [("C0001",)]
        with pytest.raises(duckdb.CatalogException, match="does not exist"):
            active.execute(inside_sql, inside_params).fetchall()


def test_it_is_freed_when_the_body_raises(reg) -> None:
    """``finally``, not a happy-path unregister.

    A value set leaked by an exception is the worst version of the leak: it
    happens exactly when the caller is least likely to notice.
    """
    with reg.session() as active:
        boom = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"), active.value_set(["C0001"]):
            raise boom
        # Scoped to value-set views by name: the schema carries a permanent
        # one of its own (#372), and counting every view made this assert
        # "no views exist" rather than "no value set leaked".
        assert reg.execute("SELECT count(*) FROM duckdb_views() WHERE view_name LIKE '_sl_values_%'")[0][0] == 0


def test_two_value_sets_do_not_collide(reg, codes) -> None:
    """Generated names, so nesting works and no caller clobbers another.

    A fixed name is the obvious implementation and is silently wrong: the
    second registration replaces the first, and the first query then filters
    on the second's values with no error.
    """
    first = [f"C{i:04d}" for i in range(10)]
    second = [f"C{i:04d}" for i in range(5, 20)]
    with reg.session() as active, active.value_set(first) as a, active.value_set(second) as b:
        assert a.name != b.name
        both = codes.filter(q.code.id.is_in(a)).filter(q.code.id.is_in(b)).ids()
    assert both == [f"C{i:04d}" for i in range(5, 10)], "each set kept its own values"


def test_it_uses_the_same_handle_the_dsl_runs_on(reg, codes) -> None:
    """The failure that motivated #300.

    Registered on any other handle — the base connection, or a second
    thread-local cache — the relation is invisible to `codes.filter(...).ids()`
    and the query fails with a catalog error. This is the assertion that would
    catch a regression in the handle consolidation.
    """
    with reg.session() as active, active.value_set(["C0001"]) as registered:
        assert codes.filter(q.code.id.is_in(registered)).ids() == ["C0001"]
