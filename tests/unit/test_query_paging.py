"""Keyset pagination — paging that does not skip or repeat.

`offset(n)` has two problems a catalogue makes real. It costs the database a
walk over `n` discarded rows, so page 500 pays for 500 pages; and it is defined
against a *position*, so a row inserted before the window between two calls
shifts everything after it — silently repeating one row and dropping another.

A cursor is defined against a *row* instead: "strictly after this one, in this
order". Inserts before it do not move it.

The design left open whether `after()` should refuse without an `order_by`. It
does not need to. Every query here already carries the root's key as a
tiebreak, so the order is always total and the cursor is always well defined —
the open question was answered by the tiebreak rather than by deciding it.

The interesting tests here are the ones that page a table **while writing to
it**, because that is the whole reason keyset exists and no static fixture
shows it.
"""

from __future__ import annotations

import annotationlib
from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query._expr import RowKind
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import CodelistRow, q
from sdmxlib.query.query import Page, Query, SliceError
from sdmxlib.storage.lazy import LazyCodelist

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Seven codelists across two agencies, so a two-key order has ties on the
    first key and the second decides — the shape a single-key test cannot
    distinguish from a broken comparison."""
    with sl.LocalRegistry(":memory:") as registry:
        for index in range(7):
            registry.add(
                Codelist(
                    id=f"CL_{index}",
                    maintainer=Agency(id="ESTAT" if index % 2 else "OECD"),
                    name={"en": f"codelist {index}"},
                    codes=[Code(id="X", name={"en": "x"})],
                )
            )
        yield registry


def _base(reg: sl.LocalRegistry) -> Query[CodelistRow, LazyCodelist]:
    return reg.query(q.codelist).order_by(q.codelist.agency, q.codelist.id)


def _walk[R: RowKind, A](query: Query[R, A], size: int) -> list[str]:
    """Every page, concatenated. The loop a caller writes."""
    found: list[str] = []
    cursor = None
    while True:
        page = query.after(cursor).page(size)
        found.extend(page.urns)
        if page.cursor is None:
            return found
        cursor = page.cursor


# ── the loop ──────────────────────────────────────────────────────────────


def test_paging_covers_every_row_exactly_once(reg: sl.LocalRegistry) -> None:
    walked = _walk(_base(reg), 3)
    assert len(walked) == 7
    assert len(set(walked)) == 7
    assert walked == _base(reg).urns()


def test_a_page_size_that_divides_evenly_still_terminates(reg: sl.LocalRegistry) -> None:
    """The off-by-one worth pinning: with 7 rows and a size of 7, the first
    page is full, so a cursor is issued and the second page is empty. A `None`
    on the full page would end the loop correctly here and incorrectly whenever
    the count is an exact multiple."""
    walked = _walk(_base(reg), 7)
    assert len(walked) == 7
    first = _base(reg).page(7)
    assert first.cursor is not None
    assert _base(reg).after(first.cursor).page(7).urns == ()


def test_a_short_page_ends_the_walk(reg: sl.LocalRegistry) -> None:
    assert _base(reg).page(100).cursor is None
    assert len(_base(reg).page(100)) == 7


def test_the_first_page_needs_no_cursor(reg: sl.LocalRegistry) -> None:
    """`after(None)` is the query unchanged, so the loop has no special case."""
    assert _base(reg).after(None).page(3).urns == _base(reg).page(3).urns


def test_a_page_of_one_walks_the_whole_table(reg: sl.LocalRegistry) -> None:
    """The smallest page exercises the most cursor comparisons."""
    assert _walk(_base(reg), 1) == _base(reg).urns()


def test_an_unsorted_query_pages_by_the_tiebreak(reg: sl.LocalRegistry) -> None:
    """Which is why `after()` needs no `order_by` precondition — arbitrary but
    stable, and stable is the whole requirement."""
    assert _walk(reg.query(q.codelist), 2) == reg.query(q.codelist).urns()


# ── what offset cannot do ─────────────────────────────────────────────────


def test_an_insert_before_the_window_does_not_shift_the_next_page(
    reg: sl.LocalRegistry,
) -> None:
    """The reason keyset exists, and it needs a write mid-walk to show.

    `CL_00` sorts before everything already read. Under `offset` the second
    page would step back over a row it had returned; under a cursor it is
    simply not after the cursor, so the walk is unaffected.
    """
    first = _base(reg).page(3)
    assert first.cursor is not None
    reg.add(Codelist(id="CL_00", maintainer=Agency(id="ESTAT"), name={"en": "inserted"}))

    rest = _walk(_base(reg).after(first.cursor), 3)
    walked = [*first.urns, *rest]
    assert len(set(walked)) == len(walked)
    assert not any("CL_00" in urn for urn in rest)


def test_offset_does_shift_under_the_same_insert(reg: sl.LocalRegistry) -> None:
    """The contrast, asserted rather than asserted-about. Without it the test
    above only says "keyset works", not "keyset fixes something"."""
    first = _base(reg).limit(3).urns()
    reg.add(Codelist(id="CL_00", maintainer=Agency(id="ESTAT"), name={"en": "inserted"}))
    second = _base(reg).offset(3).limit(3).urns()
    assert set(first) & set(second), "an insert before the window should make offset repeat a row"


def test_an_insert_after_the_cursor_is_picked_up(reg: sl.LocalRegistry) -> None:
    """A cursor is a position in an order, not a snapshot — rows arriving ahead
    of it are seen, which is the behaviour a catalogue walk wants."""
    first = _base(reg).page(3)
    assert first.cursor is not None
    reg.add(Codelist(id="CL_99", maintainer=Agency(id="OECD"), name={"en": "late"}))
    assert any("CL_99" in urn for urn in _walk(_base(reg).after(first.cursor), 3))


# ── the comparison itself ─────────────────────────────────────────────────


def test_the_second_key_decides_within_a_tie(reg: sl.LocalRegistry) -> None:
    """Four rows share `ESTAT`, so a cursor comparing only the first key would
    either skip all of them or repeat all of them."""
    estat = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").order_by(q.codelist.agency)
    assert _walk(estat, 2) == estat.urns()
    assert len(estat.urns()) == 3


def test_mixed_directions_page_correctly(reg: sl.LocalRegistry) -> None:
    """ "Newest first, then id ascending" is the common shape, and the reason
    the comparison is nested rather than SQL's row constructor — `(a, b) > (?,
    ?)` cannot express keys that disagree."""
    mixed = reg.query(q.codelist).order_by(q.codelist.agency.desc(), q.codelist.id)
    assert _walk(mixed, 2) == mixed.urns()


def test_paging_a_nullable_key_covers_every_row(reg: sl.LocalRegistry) -> None:
    """Nulls sort last, and nothing sorts after a null — get that wrong and the
    walk either stops early or loops. Codes carry a nullable `parent_id`."""
    with sl.LocalRegistry(":memory:") as codes:
        codes.add(
            Codelist(
                id="CL",
                maintainer=Agency(id="E"),
                name={"en": "x"},
                codes=[
                    Code(id="ROOT", name={"en": "r"}),
                    Code(id="A", name={"en": "a"}, parent_id="ROOT"),
                    Code(id="B", name={"en": "b"}, parent_id="ROOT"),
                    Code(id="C", name={"en": "c"}),
                ],
            )
        )
        by_parent = codes.query(q.code).order_by(q.code.parent.id)
        walked = _walk(by_parent, 2)
        assert walked == by_parent.urns()
        assert len(walked) == 4


def test_paging_composes_with_filter_and_qualify(reg: sl.LocalRegistry) -> None:
    narrowed = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").order_by(q.codelist.id)
    assert _walk(narrowed, 2) == narrowed.urns()

    latest = reg.query(q.codelist).qualify(q.codelist.version.rank(descending=True).over(q.codelist.agency) == 1)
    assert _walk(latest, 1) == latest.urns()


def test_a_page_reaches_a_non_artefact_root(reg: sl.LocalRegistry) -> None:
    """`page()` selects the root's own urn column, like `urns()` does."""
    assert _walk(reg.query(q.code), 3) == reg.query(q.code).urns()


# ── the cursor is bound to its order ──────────────────────────────────────


def test_a_cursor_from_a_differently_sorted_query_is_refused(reg: sl.LocalRegistry) -> None:
    """It carries values for keys the new order does not have, so applying it
    would compare the wrong columns and skip rows."""
    cursor = _base(reg).page(3).cursor
    assert cursor is not None
    with pytest.raises(PathError, match="only meaningful against the order"):
        reg.query(q.codelist).order_by(q.codelist.id).after(cursor).page(3)


def test_a_cursor_from_a_reversed_query_is_refused(reg: sl.LocalRegistry) -> None:
    """Same key count, opposite direction — which the length check alone would
    not catch, and which walks the table backwards from the cursor."""
    cursor = reg.query(q.codelist).order_by(q.codelist.id).page(3).cursor
    assert cursor is not None
    with pytest.raises(PathError, match="different sort direction"):
        reg.query(q.codelist).order_by(q.codelist.id.desc()).after(cursor).page(3)


def test_a_cursor_survives_being_reused(reg: sl.LocalRegistry) -> None:
    """It is a value, not a position that advances, so paging twice from the
    same cursor gives the same page."""
    cursor = _base(reg).page(2).cursor
    assert cursor is not None
    assert _base(reg).after(cursor).page(2).urns == _base(reg).after(cursor).page(2).urns


# ── refusals and laziness ─────────────────────────────────────────────────


def test_after_a_slice_is_refused(reg: sl.LocalRegistry) -> None:
    cursor = _base(reg).page(2).cursor
    assert cursor is not None
    with pytest.raises(SliceError, match=r"cannot after\(\)"):
        _base(reg).limit(2).after(cursor)


def test_a_null_cursor_after_a_slice_is_not_refused(reg: sl.LocalRegistry) -> None:
    """`after(None)` changes nothing, so refusing it would break the one
    spelling that makes the paging loop uniform."""
    assert _base(reg).limit(2).after(None).urns() == _base(reg).limit(2).urns()


def test_page_needs_a_positive_size(reg: sl.LocalRegistry) -> None:
    with pytest.raises(ValueError, match="positive"):
        _base(reg).page(0)
    with pytest.raises(ValueError, match="negative"):
        _base(reg).page(-1)


def test_after_stays_lazy(reg: sl.LocalRegistry) -> None:
    cursor = _base(reg).page(2).cursor
    assert cursor is not None
    query = _base(reg).after(cursor)
    assert "after cursor" in repr(query)
    assert "IS NOT DISTINCT FROM" in query.to_sql().sql


def test_a_page_resolves(reg: sl.LocalRegistry) -> None:
    page = _base(reg).page(2)
    assert isinstance(page, Page)
    found = page.collect()
    assert len(found) == 2
    assert all(hasattr(item, "id") for item in found)
    assert bool(page) is True


def test_every_pageable_root_keys_on_its_urn_column(reg: sl.LocalRegistry) -> None:
    """`Page.collect()` hands each URN over as a one-tuple key.

    That is right for every root `page()` accepts — it needs a `urn` column,
    and on all of those the key *is* that column — and silently wrong the day
    a root has a URN and a wider key. Asserted rather than trusted, because
    the failure would be a resolver handed the wrong shape.
    """
    for name in annotationlib.get_annotations(type(q), format=annotationlib.Format.STRING):
        accessor = type(getattr(q, name))
        urn = getattr(accessor, "urn", None)
        if urn is None:
            continue
        assert accessor.__key__ == (urn.column,), (
            f"q.{name} has a urn column but keys on {accessor.__key__}; "
            f"Page.collect() would build a one-tuple and be wrong"
        )


def test_a_page_of_codes_resolves(reg: sl.LocalRegistry) -> None:
    """The generalised resolver reaches `Page` too."""
    page = reg.query(q.code).order_by(q.code.id).page(2)
    assert [c.id for c in page.collect()] == ["X", "X"]
