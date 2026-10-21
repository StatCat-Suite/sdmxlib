"""The corpus is complete, deterministic, and binds every value (#304).

`tests/support/sql_corpus.py` is a migration equivalence check: dump the
emitted SQL before and after a refactor and diff. That only means anything if
three things hold, and none of them holds by itself.

**It must cover every predicate kind.** A hand-listed corpus goes stale exactly
when it matters — a kind added later is absent, the diff stays empty, and the
empty diff reads as proof. So the covered set is *derived* off `_expr` and
compared against what the corpus actually builds, the same shape as the
oracles' `test_every_predicate_kind_is_covered`. That is the check the previous
corpus did not have.

**It must be deterministic.** A dump that varies between runs makes every diff
noise. Alias allocation is per-compile and the accessors are module-level
singletons, so this is a real risk rather than a theoretical one.

**It must not interpolate values.** A corpus that inlined a literal would hide
the one class of bug `sql_ast` invariants exist to catch, and would also make
the diff change whenever a fixture value changed rather than when the compiler
did.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from sdmxlib.query import _expr as qx
from tests.support import sql_corpus

pytestmark = pytest.mark.unit


def _concrete_predicate_kinds() -> set[str]:
    """Every `Pred` subclass a caller can end up with, off the module.

    Derived rather than listed. `AnyPred`, `Pred` and `AggPred` are the
    abstract bases — `AggPred` is the vocabulary `having()` names, and a
    caller only ever holds its concrete form `AggCompare`, which is in the
    corpus. Everything else is a node some expression builds.

    `inspect.isabstract` does not catch these: none declares an abstract
    method, so they are abstract by intent rather than by ABC machinery.
    """
    return {
        name
        for name, value in vars(qx).items()
        if isinstance(value, type)
        and issubclass(value, qx.AnyPred)
        and value not in (qx.AnyPred, qx.Pred, qx.AggPred)
        and not inspect.isabstract(value)
    }


def _kinds_in(node: object, seen: set[str]) -> None:
    """Walk a predicate tree, recording the class name of every node."""
    if isinstance(node, qx.AnyPred):
        seen.add(type(node).__name__)
    for attr in ("parts", "part", "preds"):
        child: object = getattr(node, attr, None)
        if isinstance(child, (tuple, list)):
            for item in child:
                _kinds_in(item, seen)
        elif child is not None:
            _kinds_in(child, seen)


def _walk_cases() -> set[str]:
    """Build every case again, this time recording predicate node kinds.

    `dump()` is called first because the case builders need its connection;
    doing the walk inside `dump` would tangle the diff output with the guard.
    """
    import duckdb

    from sdmxlib.storage.schema import create_schema
    from sdmxlib.storage.writers import (
        put_codelist,
        put_concept_scheme,
        put_data_structure,
        put_dataflow,
        put_hierarchy,
    )

    conn = create_schema(duckdb.connect(":memory:"))
    put_codelist(conn, sql_corpus._codelist())
    put_hierarchy(conn, sql_corpus._hierarchy())
    put_concept_scheme(conn, sql_corpus._concept_scheme())
    put_data_structure(conn, sql_corpus._dsd())
    put_dataflow(conn, sql_corpus._dataflow())
    sql_corpus._CONN = conn

    seen: set[str] = set()
    try:
        for _section, cases in sql_corpus._sections():
            for _label, query in cases:
                # A `Select` wraps its `Query`; both shapes appear in the corpus,
                # so the unwrap is dynamic and the `Any` dies on this line.
                inner: Any = getattr(query, "_query", query)
                for pred in (*inner.predicates, *inner.qualifiers):
                    _kinds_in(pred, seen)
                if inner.cursor is not None:
                    seen.add("Cursor")
    finally:
        conn.close()
        sql_corpus._CONN = None
    return seen


def test_every_predicate_kind_appears_in_the_corpus() -> None:
    """The check the previous corpus lacked, and the reason it could go stale.

    A predicate kind absent from the corpus produces no line, so a refactor
    that changed only its emitter shows an empty diff — which reads as "nothing
    changed" rather than "nothing was looked at".
    """
    declared = _concrete_predicate_kinds()
    built = _walk_cases()

    #: Kinds a `filter()` never holds, with where they are covered instead.
    elsewhere = {
        # `RowIn` is built by `intersect` / `difference`, which store it as a
        # predicate — but only once compiled, so the walk above cannot see it.
        # The corpus emits both, and `test_query_sets.py` owns the semantics.
        "RowIn",
    }
    missing = declared - built - elsewhere
    assert not missing, (
        f"predicate kinds with no corpus case: {sorted(missing)}. "
        "Add one to `tests/support/sql_corpus.py`, or the equivalence diff "
        "will be empty for any refactor that only touches their emitter."
    )


def test_the_sets_cases_emit_their_predicate() -> None:
    """`RowIn`'s cover, since the tree walk cannot reach it."""
    dump = sql_corpus.dump()

    assert "clause  intersect" in dump
    assert "clause  difference" in dump
    assert "EXISTS" in dump
    assert "NOT EXISTS" in dump


def test_the_dump_is_deterministic() -> None:
    """Two runs, byte-identical — or every diff is noise.

    Alias allocation is per-compile and the accessors are module-level
    singletons, so a compiler that leaked state between compiles would show up
    here and nowhere else.
    """
    assert sql_corpus.dump() == sql_corpus.dump()


def test_the_corpus_binds_every_value() -> None:
    """No literal is interpolated into the SQL text.

    The corpus's fixture values are distinctive enough to search for. Finding
    one in the statement rather than the params would mean the compiler
    inlined it — the class of bug `test_sql_ast_invariants.py` exists for, and
    one that would also make this diff churn on fixture edits.
    """
    for line in sql_corpus.dump().splitlines():
        if "| params=" in line:
            continue
        sql = line.split("|", 1)[1]
        for literal in ("'Alpha'", "'Beta'", "'note'", "'Region'"):
            assert literal not in sql, f"literal {literal} inlined: {sql.strip()}"


def test_every_section_is_populated() -> None:
    """A section that silently emptied would shrink the corpus without failing.

    Counts rather than exact contents: the point is that each group of cases
    still produces lines, not that any particular case does.
    """
    counts: dict[str, int] = {}
    for line in sql_corpus.dump().splitlines():
        counts[line.split()[0]] = counts.get(line.split()[0], 0) + 1

    assert set(counts) == {"scalar", "hop", "bool", "clause", "frame"}
    for section, n in counts.items():
        assert n >= 16, f"section {section!r} has only {n} lines"
