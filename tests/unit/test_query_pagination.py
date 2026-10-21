"""`offset` and its composition with `limit`, on all four query DSLs (#264).

`limit` existed; `offset` did not, so there was no way to ask for the second
page of anything and the documented answer was to drop to raw SQL.

## Why `max` and not accumulate

The composition law follows from a question the issue had not asked: is this
DSL a pipeline or declarative? Measured — it is declarative. Every method
contributes a clause to one ``SELECT``, so ``q.limit(3).filter(p)`` and
``q.filter(p).limit(3)`` are the same query, where polars' equivalents differ.

A query therefore owns a window ``[start, start+length)``: `offset` raises
``start``, `limit` lowers ``length``, and neither moves back. Both are then
idempotent and order-independent, which is what lets a helper add a clause to a
query it did not build. The cursor reading — "offset advances, so two offsets
advance twice" — is the *pipeline* reading, and would make `offset` the only
stateful operation in a declarative surface.

Nobody else uses `max`, because the ecosystem splits by API shape rather than
by semantics: slice-shaped APIs accumulate (Django ``qs[5:][3:]``,
elasticsearch-dsl, polars ``slice``, ibis), setter-shaped ones replace
(SQLAlchemy, peewee). But Django clamps its *limit* exactly as `narrow_limit`
does — ``qs[:5][:10]`` is five rows — so refinement is not our invention, and
SQLAlchemy's last-wins is a slot on a statement object rather than a
composition law.

## Two pre-existing defects this found

Both were latent under `limit` alone and would have been inherited by `offset`,
so they are fixed here and pinned below:

- ``to_sql()`` dropped ``ORDER BY`` for subquery use. Harmless for an unbounded
  set; wrong for a *sliced* one, where the order decides which rows come back.
- ``len()`` applied the slice to ``COUNT(*)``, which counts every row and then
  limits the one-row result — so ``len(q.limit(3))`` returned the full count.
"""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.query.accessors import CodeRow, q
from sdmxlib.query.query import Query
from tests.support.factories import code_urn
from tests.support.narrow import present

pytestmark = pytest.mark.unit

#: Twenty codes, not ten. With ten, ``offset(5).offset(10)`` is empty under
#: *both* `max` (skip 10) and accumulation (skip 15), so the fixture could not
#: tell the two laws apart — measured, after the first draft did exactly that.
_N = 20


@pytest.fixture
def codes():
    cl = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[Code(id=f"C{i:02d}", name={"en": f"Name {i}"}) for i in range(_N)],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


# ── The window: start rises, length shrinks ──────────────────────────────────


def test_offset_skips_from_the_front(codes) -> None:
    assert codes.offset(3).ids() == codes.ids()[3:]


def test_limit_and_offset_are_a_slice(codes) -> None:
    """Stated as a slice, not a length.

    A length assertion (``len(...) == 2``) passes even if the ``OFFSET`` were
    applied before the ``ORDER BY``, or dropped entirely on the compile path
    that omits ordering. The slice pins *which* rows.
    """
    everything = codes.ids()
    assert codes.offset(3).limit(2).ids() == everything[3:5]
    assert codes.offset(0).limit(5).ids() == everything[:5]
    assert codes.offset(_N - 2).limit(5).ids() == everything[-2:]


def test_limit_and_offset_do_not_commute_and_that_is_the_trade(codes) -> None:
    """The one order-sensitive pair in the DSL, pinned deliberately.

    ``offset(3).limit(2)`` is "two rows starting at three". ``limit(2)
    .offset(3)`` is "of the first two, skip three" — empty. Both read left to
    right, and the second is a reasonable thing to want.

    This is a *chosen* cost. Narrowing, idempotence and commutativity are not
    simultaneously available: SQL's ``LIMIT n OFFSET m`` gets commutativity by
    making ``n`` a length from ``m``, which lets a later `offset` slide the
    window past rows an earlier `limit` had excluded — measured, and the reason
    the first implementation of #264 was reworked. `advance_window` has the
    argument.

    Every other composition in the DSL commutes, and #296 proposes making the
    remaining ambiguous sequences errors, so the exposure is this pair alone.
    """
    assert codes.offset(3).limit(2).ids() == codes.ids()[3:5]
    assert codes.limit(2).offset(3).ids() == []
    assert codes.limit(10).offset(5).ids() == codes.ids()[5:10]
    assert codes.offset(5).limit(10).ids() == codes.ids()[5:15]


def test_an_offset_past_the_end_is_empty_not_an_error(codes) -> None:
    assert codes.offset(_N).ids() == []
    assert codes.offset(_N * 10).ids() == []
    assert codes.offset(_N * 10).limit(5).ids() == []


# ── The composition law ──────────────────────────────────────────────────────


def test_composing_offsets_takes_the_larger(codes) -> None:
    """`max`: the floor rises and never falls.

    All three assertions discriminate on this fixture. Under accumulation the
    first two would start at ``C15``; under last-wins the second would start at
    ``C05``, widening what an earlier caller had narrowed.
    """
    assert codes.offset(5).offset(10).ids() == codes.ids()[10:]
    assert codes.offset(10).offset(5).ids() == codes.ids()[10:]
    assert codes.offset(10).offset(10).ids() == codes.ids()[10:]


def test_offset_is_idempotent(codes) -> None:
    """Applying the same constraint twice changes nothing.

    The property accumulation gives up, and the reason `max` is the dual of
    `narrow_limit` rather than merely similar to it.
    """
    once = codes.offset(7)
    assert once.offset(7).ids() == once.ids()


def test_composition_never_widens(codes) -> None:
    """The invariant `limit` and `offset` share: every call narrows.

    Asserted as set containment rather than on specific ids, because that is
    the actual claim — a later caller cannot recover a row an earlier one
    excluded.
    """
    base = codes.offset(5).limit(10)
    for narrowed in (base.offset(2), base.offset(8), base.limit(3), base.limit(20)):
        assert set(narrowed.ids()) <= set(base.ids())

    # The case that falsified the first design. Under a `(start, length)`
    # window with the length held constant, `base.offset(8)` returned rows
    # 8..18 — three rows `base` had excluded, a caller undoing a helper's cap.
    assert base.offset(8).ids() == codes.ids()[8:15]


# ── Guards ───────────────────────────────────────────────────────────────────


def test_offset_rejects_non_ints(codes) -> None:
    """Same guard as `limit`, for the same reason: ``limit("5")`` once reached
    DuckDB and *worked*, which is worse than failing (#266).
    """
    with pytest.raises(TypeError, match="takes an int"):
        codes.offset("3")
    with pytest.raises(TypeError, match="takes an int"):
        codes.offset(True)  # bool is an int subclass and never intended
    with pytest.raises(TypeError, match="takes an int"):
        codes.offset(1.0)


def test_offset_rejects_negatives(codes) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        codes.offset(-1)


def test_offset_zero_is_allowed_and_is_a_no_op(codes) -> None:
    assert codes.offset(0).ids() == codes.ids()


# ── The two defects this found ───────────────────────────────────────────────


def test_a_sliced_subquery_keeps_its_order(codes: Query[CodeRow, Code]) -> None:
    """``to_sql()`` drops ``ORDER BY`` only when the query is unbounded.

    Inside ``WHERE key IN (...)`` the order of a *set* cannot matter, which is
    why it was dropped. Once the query is sliced the order decides *which* rows
    the set contains, so dropping it makes the subquery non-deterministic —
    latent under `limit` before this, and it would have made a paged subquery
    actively wrong.

    Asserted through `intersect` since #306, which is where the rule now lives:
    a bare `to_sql()` always orders, so that `ids()` is stable across calls,
    and it is the *relation* form — `Query._to_relation_sql`, reached by
    `intersect`, `difference` and `after` — that decides whether the nested
    query's order is carried.
    """
    everything = codes.filter(q.code.id != "")

    def spliced(inner: Query[CodeRow, Code]) -> str:
        sql, _ = everything.intersect(inner).to_sql()
        spliced_in = sql[sql.index("EXISTS (") :]
        return spliced_in[: spliced_in.rindex(")") + 1]

    assert "ORDER BY" not in spliced(codes)
    assert "ORDER BY" in spliced(codes.limit(3))
    assert "ORDER BY" in spliced(codes.offset(3))
    assert "ORDER BY" in spliced(codes.offset(3).limit(2))

    # And the paged subquery returns the page, not an arbitrary slice.
    page = codes.offset(3).limit(2)
    assert everything.intersect(page).ids() == codes.ids()[3:5]


def test_len_honours_the_slice(codes) -> None:
    """``len(q.limit(3))`` was the full count.

    ``SELECT COUNT(*) ... LIMIT 3`` counts every row and then limits the
    *one-row result*, so the limit had no effect on the number returned. The
    fix wraps: ``SELECT COUNT(*) FROM (<the row query>)``.
    """
    assert codes.count() == _N
    assert codes.limit(3).count() == 3
    assert codes.offset(_N - 2).count() == 2
    assert codes.offset(5).limit(3).count() == 3
    assert codes.offset(_N * 10).count() == 0


def test_len_agrees_with_the_rows_it_would_return(codes) -> None:
    """The invariant behind the fix, stated once over every slice shape.

    Stronger than the example above and the thing a caller actually relies on:
    ``len(q)`` must be the number of rows ``q`` yields.
    """
    for query in (
        codes,
        codes.limit(3),
        codes.offset(4),
        codes.offset(4).limit(3),
        codes.offset(_N - 1).limit(10),
        codes.offset(_N * 10),
        codes.filter(q.code.id.str.starts_with("C0")).offset(2).limit(3),
    ):
        assert query.count() == len(query.ids())


def test_an_unsliced_count_stays_a_bare_aggregate(codes) -> None:
    """The wrap is only for slices; an ordinary count keeps its one statement.

    ``count()`` is called far more often than ``count()`` on a slice, so the
    fix must not put a subquery on the common path. Asserted on the compiled
    SQL, because asserting the *count* cannot tell a wrapped statement from a
    bare one — both return the right number.
    """
    bare = codes.to_sql(columns="count(*)").sql
    assert "count(*)" in bare
    assert "FROM (" not in bare


# ── All four DSLs ────────────────────────────────────────────────────────────


def test_the_hierarchy_dsl_pages() -> None:
    h = Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "H"},
        tree=[HierarchicalCode(id=f"N{i:02d}", code=code_urn("CL", f"N{i:02d}")) for i in range(10)],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(h)
        nodes = present(reg.get(Hierarchy.urn_for(agency="AG", id="H", version="1.0"))).nodes
        assert nodes.offset(3).limit(2).ids() == nodes.ids()[3:5]
        assert nodes.offset(2).offset(5).ids() == nodes.ids()[5:]
        assert nodes.limit(4).count() == 4


# `test_the_artefact_spine_pages` went with `ArtefactQuery` (#306); the
# path language's keyset paging is `tests/unit/test_query_paging.py`.
def test_the_frame_path_pages(codes) -> None:
    """`select` and `group_by` carry the slice too, not just the row path."""
    frame = codes.offset(3).limit(2).select(q.code.id).collect()
    assert frame["id"].to_list() == codes.ids()[3:5]

    projected = codes.select(q.code.id).offset(3).limit(2).collect()
    assert projected["id"].to_list() == codes.ids()[3:5]


def test_paging_covers_every_row_exactly_once(codes) -> None:
    """The end-to-end claim a pager depends on.

    Walks the whole codelist in pages of three and asserts the concatenation is
    the unpaged result — which is only true if the order is stable across
    calls, the reason every query appends a deterministic tiebreak.
    """
    size = 3
    walked: list[str] = []
    for page_no in range((_N + size - 1) // size):
        walked.extend(codes.offset(page_no * size).limit(size).ids())
    assert walked == codes.ids()
