"""Intersecting two subtrees without either operand's ids reaching Python (#375).

#348 filed this at ~40 s and it is 108 ms since the #349 peephole, so the
argument that survives is not latency. It is that

    set(a.narrower_transitive(x).ids()) & set(b.narrower_transitive(y).ids())

costs what the *operands* weigh rather than what the answer weighs, and pays it
on the **Python heap**, which is outside DuckDB's `memory_limit` — a container
caps the engine and not the interpreter. On the census bundle's
`H_CP_GEO_FED` x `H_CP_GEO_POPCTR`, both rooted at Canada:

| | wall | peak Python heap |
| --- | --- | --- |
| two id lists, intersected in Python | 235.6 ms | **+80.7 MB** |
| one statement, `count()` | 54.7 ms | **+0.0 MB** |

Same answer, 265,939 rows.

**No new API was needed**, which is the outcome #375 hoped for: `is_in` already
takes an `sdmxlib.sql.SqlRelation`, and a `select()` projection already is one.
These tests are that claim, asserted on **rows fetched** rather than wall time —
the timing is fine either way, so a timing assertion would pass whether or not
the work stayed in the engine.
"""

from __future__ import annotations

from typing import Any

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.query.accessors import q
from tests.support.factories import code_urn

pytestmark = pytest.mark.unit


class _RowCounter:
    """Connection proxy counting the rows that cross into Python.

    Executes are not the measure here — both forms run one or two statements.
    What separates them is how much comes *back*, which is the whole of #375.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.rows = 0
        self._parent: _RowCounter | None = None

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        return _CountingResult(self._inner.execute(sql, *args, **kwargs), self)

    def cursor(self) -> Any:
        return _RowCounter(self._inner.cursor())._sharing(self)

    def _sharing(self, parent: _RowCounter) -> _RowCounter:
        self._parent = parent
        return self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def record(self, n: int) -> None:
        self.rows += n
        parent = self._parent
        if parent is not None:
            parent.record(n)


class _CountingResult:
    def __init__(self, inner: Any, counter: _RowCounter) -> None:
        self._inner = inner
        self._counter = counter

    def fetchall(self) -> Any:
        rows = self._inner.fetchall()
        self._counter.record(len(rows))
        return rows

    def fetchone(self) -> Any:
        row = self._inner.fetchone()
        if row is not None:
            self._counter.record(1)
        return row

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


#: Two hierarchies over one codelist, overlapping in a known, non-empty way.
#:
#: The overlap has to be non-empty or the whole file is vacuous: the
#: country-scale pair in #348 intersects to **zero** rows, and a test built on
#: it would pass against an implementation that returned nothing at all.
#: The census bundle's `H_CP_GEO_ADMINISTRATIVE` x `H_CP_GEO_POPCTR` share exactly
#: their root, which is why the measurements above use `H_CP_GEO_FED` instead.
_SHARED = ("N10", "N11", "N12")
_ONLY_A = ("N20", "N21")
_ONLY_B = ("N30", "N31", "N32", "N33")


@pytest.fixture
def two_subtrees(monkeypatch: pytest.MonkeyPatch):
    """A registry holding two hierarchies rooted at the same node id."""
    ids = ("ROOT", *_SHARED, *_ONLY_A, *_ONLY_B)
    codelist = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "cl"},
        codes=[Code(id=i, name={"en": i}) for i in ids],
    )

    def hierarchy(hid: str, leaves: tuple[str, ...]) -> Hierarchy:
        return Hierarchy(
            id=hid,
            maintainer="AG",
            version="1.0",
            name={"en": hid},
            tree=[
                HierarchicalCode(
                    id="ROOT",
                    code=code_urn("CL", "ROOT"),
                    children=[HierarchicalCode(id=i, code=code_urn("CL", i)) for i in leaves],
                )
            ],
        )

    with LocalRegistry(":memory:") as registry:
        registry.add_all(
            [
                codelist,
                hierarchy("H_A", (*_SHARED, *_ONLY_A)),
                hierarchy("H_B", (*_SHARED, *_ONLY_B)),
            ]
        )
        counter = _RowCounter(registry._conn)
        # Replacing internals for the count. Through `monkeypatch` because the
        # declared types are the real connection and cursor, not a counting
        # proxy — and because it undoes itself when the fixture unwinds.
        monkeypatch.setattr(registry, "_conn", counter)
        monkeypatch.setattr(registry, "_cursor", counter)
        import threading

        registry._thread_local = threading.local()
        a = registry.get(Hierarchy.urn_for(agency="AG", id="H_A", version="1.0"))
        b = registry.get(Hierarchy.urn_for(agency="AG", id="H_B", version="1.0"))
        yield a, b, counter


def _workaround(a, b) -> set[str]:
    """What a caller writes without this — #348's own line."""
    return set(a.narrower_transitive("ROOT").ids()) & set(b.narrower_transitive("ROOT").ids())


def _pushed(a, b):
    """The same question, kept in the engine.

    `order_by(None)` because a relation used for *membership* has no use for
    an order, and the projection would otherwise carry the root-key tiebreak
    into the subquery — measured at ~12% on the real bundle (#389).
    """
    other = b.narrower_transitive("ROOT").order_by(None).select(q.hcode.id)
    return a.narrower_transitive("ROOT").filter(q.hcode.id.is_in(other))


def test_the_two_forms_agree(two_subtrees) -> None:
    a, b, _ = two_subtrees
    assert _workaround(a, b) == set(_SHARED)
    assert set(_pushed(a, b).ids()) == set(_SHARED)


def test_the_projection_is_a_relation_not_a_materialised_list(two_subtrees) -> None:
    """The claim that makes this work without a new primitive: `select()`
    already satisfies `sdmxlib.sql.SqlRelation`, and `is_in` already takes one.
    """
    _, b, _ = two_subtrees
    projection = b.narrower_transitive("ROOT").order_by(None).select(q.hcode.id)
    assert projection.columns == ("id",), "one column, which is what IN (...) needs"
    assert hasattr(projection, "to_sql"), "and it compiles rather than fetching"


def test_the_pushed_form_compiles_to_one_statement_with_a_subquery(two_subtrees) -> None:
    a, b, _ = two_subtrees
    sql = _pushed(a, b).to_sql().sql
    assert "IN (SELECT" in sql, f"the operand is a subquery, not binds: {sql}"
    assert "?" in sql, "and its own filters are still parameterised"


def test_counting_the_intersection_fetches_one_row(two_subtrees) -> None:
    """The acceptance criterion, on rows fetched rather than on the clock.

    The workaround's cost is set by the operands and is paid whatever the
    answer is — on the real bundle that is 499,137 + 266,965 ids for an answer
    of 265,939, and 80.7 MB of Python heap for an answer that is one integer.
    """
    a, b, counter = two_subtrees

    counter.rows = 0
    expected = len(_workaround(a, b))
    round_tripped = counter.rows

    counter.rows = 0
    pushed = _pushed(a, b)
    assert counter.rows == 2, "building it: one row per bare-key walk, the #514 id-not-path lookup"
    counter.rows = 0
    assert pushed.count() == expected
    pushed_rows = counter.rows

    assert pushed_rows == 1, f"a count is one row; fetched {pushed_rows}"
    assert round_tripped >= len(_SHARED) + len(_ONLY_A) + len(_ONLY_B), (
        f"the workaround should pull both operands, got {round_tripped}"
    )
    assert pushed_rows < round_tripped, (
        f"pushed {pushed_rows} rows vs {round_tripped} — the point is that the cost stops being set by the operands"
    )


def test_the_intersection_itself_fetches_only_its_answer(two_subtrees) -> None:
    """Not just `count()`: asking for the rows fetches the *answer*, where the
    workaround still fetches both operands first."""
    a, b, counter = two_subtrees

    counter.rows = 0
    _workaround(a, b)
    round_tripped = counter.rows

    pushed = _pushed(a, b)
    counter.rows = 0
    assert set(pushed.ids()) == set(_SHARED)
    pushed_rows = counter.rows

    assert pushed_rows == len(_SHARED), f"the answer and nothing else, got {pushed_rows}"
    assert pushed_rows < round_tripped
