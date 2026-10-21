"""`order_by` replaces, takes several keys, and clears (#294).

It used to append, so the **first** caller's key dominated:

    q.order_by(q.code.depth).order_by(q.code.id)   # ORDER BY depth, code_id, <tiebreak>

A helper that sorted by a unique column therefore made a caller's later
`order_by` a *silent no-op* — the tiebreak never fired, nothing errored, and the
caller simply got the wrong order.

That came from modelling `order_by` on the refinement principle by symmetry with
`limit`, which was a category error: refinement is a claim about **which rows
come back**, and ordering does not change the row set. The principle now says
so explicitly (`docs/design/query-composition.md`).

Two of three declarative peers make the later call dominant — Django replaces,
ibis prepends — and only SQLAlchemy appends, which is survivable there because
it also offers `order_by(None)`. We appended *and* had no reset, the worst
pairing available.

`with_annotations` is the same shape and was already answered this way: it
selects what to hydrate rather than which rows, so it replaces and clears. This
brings `order_by` into line with the in-repo precedent, not just the ecosystem.

## Why the fixture looks like this

`depth` and `id` must induce *different* orders, or a test cannot tell replace
from append. `C0` at depth 0 and `C1`/`C2` beneath it give `depth` a different
answer from `id` reversed, which is what makes the assertions discriminate.
"""

import importlib.util

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.query._common import canonical_key
from sdmxlib.query.accessors import q
from sdmxlib.query.query import SliceError
from tests.support.factories import code_urn
from tests.support.narrow import present

pytestmark = pytest.mark.unit


@pytest.fixture
def codes():
    """Three codes where `depth` and `id` disagree about the order."""
    cl = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[
            Code(id="C0", name={"en": "zero"}),
            Code(id="C1", name={"en": "one"}, parent_id="C0"),
            Code(id="C2", name={"en": "two"}, parent_id="C1"),
        ],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


# ── Replace, not append ──────────────────────────────────────────────────────


def test_a_later_sort_by_replaces_an_earlier_one(codes) -> None:
    """The defect, inverted into an assertion.

    Under the old append the result would be `depth, code_id` — ascending by
    depth — and the caller's descending id would never be reached. The fixture
    is built so the two answers differ; a codelist where depth and id agree
    could not tell them apart.
    """
    assert codes.order_by(q.code.depth).ids() == ["C0", "C1", "C2"]
    assert codes.order_by(q.code.id.desc()).ids() == ["C2", "C1", "C0"]
    assert codes.order_by(q.code.depth).order_by(q.code.id.desc()).ids() == ["C2", "C1", "C0"]


def test_the_caller_wins_not_the_helper(codes) -> None:
    """The scenario the defect was reported as, written out.

    A helper hands back a sorted query; the caller wants a different order and
    says so. Before #294 the caller's statement was silently ignored.
    """

    def helper(query):
        return query.order_by(q.code.id)  # a unique column — the tiebreak never fires

    assert helper(codes).order_by(q.code.depth.desc()).ids() == ["C2", "C1", "C0"]


def test_several_keys_go_in_one_call(codes) -> None:
    """The capability append provided, kept — just with an explicit spelling.

    Chaining was never the point; multi-key ordering was. One call states the
    full key list, so there is no question about which key dominates.
    """
    assert codes.order_by(q.code.depth, q.code.id).ids() == ["C0", "C1", "C2"]
    assert codes.order_by(q.code.depth.desc(), q.code.id).ids() == ["C2", "C1", "C0"]

    query = codes.order_by(q.code.depth, q.code.id)
    assert len(query.order_keys) == 2


def test_direction_is_per_key(codes) -> None:
    """One call, two keys, two directions — the shape a per-call flag cannot express.

    This is why the direction moved onto the key. `descending=[False, True]`
    parallel to the key list is polars' answer; SQLAlchemy, ibis and Django all
    mark the key instead, and a parallel array puts the direction at a distance
    from what it describes.
    """
    frame = codes.select(q.code.depth, q.code.id).order_by(q.code.depth.desc(), q.code.id).collect()
    assert frame["depth"].to_list() == [2, 1, 0]

    frame = codes.select(q.code.depth, q.code.id).order_by(q.code.depth, q.code.id.desc()).collect()
    assert frame["depth"].to_list() == [0, 1, 2]


# ── Clearing ─────────────────────────────────────────────────────────────────


def test_no_arguments_clears_the_order(codes) -> None:
    """`order_by()` resets, exactly as Django's `order_by()` does.

    The escape hatch every accumulating peer provides and we did not. Without
    it a query that had been sorted could never be un-sorted, only rebuilt from
    the artefact.
    """
    sorted_query = codes.order_by(q.code.id.desc())
    assert sorted_query.ids() == ["C2", "C1", "C0"]
    assert sorted_query.order_by().ids() == codes.ids()
    assert sorted_query.order_by().order_keys == ()


def test_clearing_leaves_the_deterministic_tiebreak(codes) -> None:
    """Clearing removes the caller's keys, not the compiler's total order.

    Paging depends on the order being total and stable across calls (#264), so
    an empty `order_by` must not make the result arbitrary.
    """
    cleared = codes.order_by(q.code.id.desc()).order_by()
    assert cleared.ids() == codes.ids()
    assert "ORDER BY" in cleared.limit(2).to_sql()[0]
    assert cleared.limit(2).ids() == codes.ids()[:2]


# ── Ordering is not a refinement ─────────────────────────────────────────────


def test_ordering_never_changes_the_row_set(codes) -> None:
    """The law that governs `order_by` instead of refinement.

    Stronger than monotonicity, and the reason the refinement principle
    explicitly excludes ordering: a `order_by` that dropped rows would satisfy
    "narrows" and be badly wrong.
    """
    everything = set(codes.ids())
    for query in (
        codes.order_by(q.code.id),
        codes.order_by(q.code.id.desc()),
        codes.order_by(q.code.depth.desc(), q.code.id),
        codes.order_by(),
    ):
        assert set(query.ids()) == everything


# ── Misuse ───────────────────────────────────────────────────────────────────


def test_a_predicate_is_still_rejected_and_the_message_mentions_desc(codes) -> None:
    """`order_by(q.code.id == "A")` is the plausible slip, and the fix is named.

    The message now also names `.desc()`, since that is the other thing someone
    reaching for `ascending=False` needs to find.
    """
    with pytest.raises(TypeError, match="takes a column") as exc:
        codes.order_by(q.code.id == "C0")
    assert ".desc()" in str(exc.value)


def test_the_removed_keyword_fails_loudly(codes) -> None:
    """`ascending=` is gone; it cannot express per-key direction.

    Pinned so the removal is a visible decision rather than something that
    quietly starts being ignored.
    """
    with pytest.raises(TypeError):
        codes.order_by(q.code.id, ascending=False)


# ── The two families stay apart ──────────────────────────────────────────────


def test_sql_queries_say_order_by_and_in_memory_collections_say_sort_by() -> None:
    """The rename is a *split*, not a global search-and-replace (#295).

    The SQL DSLs compile a clause, so they use the clause name and the ordering
    is expressed with field references and `.desc()`. `ItemList`,
    `ArtefactList` sort in Python, take a key function or an
    attribute name, and mirror ``sorted(key=, reverse=)`` down to the
    ``descending=`` flag — so they keep `sort_by`.

    Asserted in both directions. Only checking that the SQL side gained
    `order_by` would let an alias creep back, and only checking the in-memory
    side would not notice a well-meaning rename there. The names being
    different is the signal that the mechanisms are.
    """
    from sdmxlib.model.collections import ArtefactList, ItemList
    from sdmxlib.query.query import Query as PathQuery

    # `CodeQuery` and `HierarchyNodeQuery` were the last two here and went in
    # #306. The path language is the only SQL query left, and it kept the
    # naming split this test exists to protect.
    assert hasattr(PathQuery, "order_by")
    assert not hasattr(PathQuery, "sort_by"), "the SQL query gained the in-memory spelling"

    for cls in (ItemList, ArtefactList):
        assert hasattr(cls, "sort_by"), cls.__name__
        assert not hasattr(cls, "order_by"), f"{cls.__name__} was renamed by mistake"

    # `api.Query` used to sit in the second group, then carried neither verb
    # (#306), and is now gone entirely (#312): its `sort_by` sorted *after* the
    # fetch, so it was a materialised collection wearing a query's spelling,
    # and strict laziness left it with nothing of its own. Asserted as absence
    # of the class, which subsumes the two `hasattr` checks it replaces.
    assert importlib.util.find_spec("sdmxlib.api.query") is None, "and its module went with reg.data() (#516)"


# ── Every DSL ────────────────────────────────────────────────────────────────


def test_the_hierarchy_dsl_replaces_too() -> None:
    h = Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "H"},
        tree=[
            HierarchicalCode(
                id="N0",
                code=code_urn("CL", "N0"),
                children=[HierarchicalCode(id="N1", code=code_urn("CL", "N1"))],
            )
        ],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(h)
        nodes = present(reg.get(Hierarchy.urn_for(agency="AG", id="H", version="1.0"))).nodes
        assert nodes.order_by(q.hcode.depth).order_by(q.hcode.id.desc()).ids() == ["N1", "N0"]
        assert nodes.order_by(q.hcode.id.desc()).order_by().ids() == nodes.ids()


# `test_the_artefact_spine_replaces_too` went with `ArtefactQuery` (#306).
# The path language's `order_by` replacement is covered by
# `tests/unit/test_query_order.py`.
def test_an_aggregate_can_be_a_descending_key(codes) -> None:
    """`agg.desc()` — the top-N frame, which is why the query stays lazy.

    Sorting a materialised frame has already fetched every row; this asks the
    database for the top one.
    """
    n = q.code.id.count()
    frame = codes.group_by(q.code.parent.id).agg(n=n).order_by(n.desc()).limit(1).collect()
    assert len(frame) == 1


# ── `order_by(None)` declines the total order (#389) ─────────────────────────


def test_none_drops_the_order_by_entirely(codes) -> None:
    """SQLAlchemy's spelling for it, and a *third* state.

    `order_by()` clears the caller's keys and leaves the root-key tiebreak;
    this drops that too, which is the only way to reach a statement with no
    `ORDER BY` short of `stream()`. On the census bundle that clause was 5.4x the
    scan it decorated — 337 ms against 62 ms on a 4.26 M-row partition (#389).
    """
    assert "ORDER BY" not in codes.order_by(None).to_sql().sql
    assert codes.order_by(None).unordered is True


def test_the_default_is_unchanged(codes) -> None:
    """The guard the issue asks for by name.

    A default quietly becoming unordered is a silent change to what every
    existing caller receives, so it is asserted on the SQL rather than left to
    follow from the tests above passing.
    """
    for query in (codes, codes.order_by(), codes.order_by(q.code.id)):
        assert "ORDER BY" in query.to_sql().sql
    assert codes.unordered is False
    assert codes.order_by().unordered is False, "clearing the keys is not declining the order"


def test_declining_the_order_does_not_change_the_row_set(codes) -> None:
    """The same law `order_by` obeys — see `test_ordering_never_changes_the_row_set`.

    Worth restating for this spelling: dropping a clause that decides *which*
    rows come back would be a different and much worse defect than dropping one
    that decides their sequence.
    """
    assert set(codes.order_by(None).ids()) == set(codes.ids())


def test_a_later_order_by_reopens_it(codes) -> None:
    """`order_by` replaces the ordering outright, which is already its
    documented semantics — so naming keys after `None` is not ignored."""
    reopened = codes.order_by(None).order_by(q.code.id.desc())
    assert reopened.unordered is False
    assert reopened.ids() == ["C2", "C1", "C0"]
    assert "ORDER BY" in reopened.to_sql().sql


def test_none_cannot_be_one_of_several_keys(codes) -> None:
    """It cancels the order; it is not a key that happens to be null."""
    with pytest.raises(TypeError, match="cannot be one of 2 keys"):
        codes.order_by(None, q.code.id)
    with pytest.raises(TypeError, match="cannot be one of 2 keys"):
        codes.order_by(q.code.id, None)


def test_it_survives_further_refinement(codes) -> None:
    """A plan field, not a terminal flag — so a helper can decline the order
    and the caller can still filter."""
    narrowed = codes.order_by(None).filter(q.code.id != "C0")
    assert narrowed.unordered is True
    assert "ORDER BY" not in narrowed.to_sql().sql
    assert set(narrowed.ids()) == {"C1", "C2"}


# ── and it closes the query to windows ───────────────────────────────────────


@pytest.mark.parametrize("clause", ["limit", "offset", "page"])
def test_a_window_over_an_unordered_query_is_refused(codes, clause: str) -> None:
    """A `limit` decides *which* rows come back, so it needs the order to be
    total and stable between calls. Declining the tiebreak and then paging
    would reintroduce #264 silently, so the two are made exclusive."""
    with pytest.raises(SliceError, match="unordered query"):
        getattr(codes.order_by(None), clause)(2)


def test_a_cursor_against_an_unordered_query_is_refused(codes) -> None:
    """A cursor is made of the sort keys, so an unordered query has none to
    build one from."""
    cursor = codes.page(2).cursor
    assert cursor is not None
    with pytest.raises(SliceError, match="unordered query"):
        codes.order_by(None).after(cursor)


def test_the_refusal_names_both_ways_out(codes) -> None:
    """A message that says only "no" leaves the caller to guess which of the
    two things they wanted has to give."""
    with pytest.raises(SliceError) as raised:
        codes.order_by(None).limit(2)
    text = str(raised.value)
    assert "order_by()" in text, "restore the tiebreak alone"
    assert "stream()" in text, "or read it whole, unordered and bounded"


def test_declining_the_order_after_slicing_is_refused_too(codes) -> None:
    """The existing law, reached by the new spelling: a different order over
    the same window is a different set of rows."""
    with pytest.raises(SliceError, match="has been sliced"):
        codes.limit(2).order_by(None)


# ── the reducing terminals still work ────────────────────────────────────────


def test_the_terminals_that_reduce_do_not_need_an_order(codes) -> None:
    """`exists`, `one_or_none`, `count` and `preview` cap their rows without
    being a window anything pages from, so `_window` bypasses the guard rather
    than refusing and naming a method the caller never wrote."""
    unordered = codes.order_by(None)
    assert unordered.count() == 3
    assert unordered.exists() is True
    assert len(unordered.preview(2)) == 2
    assert unordered.filter(q.code.id == "C1").one_or_none() is not None


def test_first_returns_an_arbitrary_match_rather_than_refusing(codes) -> None:
    """ "First" in no order is undefined, and that is what the caller asked for.
    SQLAlchemy's `.first()` on an unordered statement does the same."""
    found = codes.order_by(None).first()
    assert found is not None
    assert found.id in {"C0", "C1", "C2"}


# ── it is part of the question, so it is part of the key ─────────────────────


def test_an_unordered_query_keys_differently(codes) -> None:
    """`canonical_key` is what a consumer caches on (#270), and two queries
    that emit different SQL must not share a key — a cache serving the ordered
    result for the unordered one would be the same class of defect as serving
    one page order for another."""
    assert canonical_key(codes.plan) != canonical_key(codes.order_by(None).plan)
    assert canonical_key(codes.order_by().plan) != canonical_key(codes.order_by(None).plan)


def test_the_repr_says_so(codes) -> None:
    """It is a lazy query, so `repr` is the only place the state is visible
    before a terminal runs."""
    assert "unordered" in repr(codes.order_by(None))
    assert "unordered" not in repr(codes)


def test_declining_the_order_on_a_projection_names_the_right_call(codes) -> None:
    """A projection has no row order of its own — it inherits the query's — so
    the generic "NoneType is not among (id)" would read as a typo rather than
    as a call in the wrong place."""
    with pytest.raises(TypeError, match=r"before\s+select\(\)"):
        codes.select(q.code.id).order_by(None)
