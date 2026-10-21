"""`Query` composes by refinement and executes only at a terminal.

The composition tests are the ones with history behind them: every law here was
paid for by a defect, and the docstrings name which. The laziness tests are the
other half — a terminal that runs when it should not is invisible until a
profile says so.
"""

from __future__ import annotations

from collections.abc import Sequence

import duckdb
import pytest

from sdmxlib.model.datastructure import DataStructure
from sdmxlib.query.accessors import DsdRow, q
from sdmxlib.query.query import Query, SliceError, build
from sdmxlib.storage.schema import create_schema

pytestmark = pytest.mark.unit


def _closed_connection() -> duckdb.DuckDBPyConnection:
    """A connection that raises on any use.

    `None` was passed here before, which `build` does not declare and only
    tolerates because these tests never touch the connection — the same
    undeclared-tolerance shape #370 was about. A closed connection makes the
    same point more strongly: anything that executed would raise
    `ConnectionException` rather than `AttributeError` on `None`.
    """
    conn = duckdb.connect()
    conn.close()
    return conn


def _echo_keys(keys: Sequence[tuple[object, ...]], *, annotations: bool = True) -> Sequence[object]:
    """A `Resolver` that hands the key rows straight back.

    `list` was passed here before, which satisfies the positional call and
    nothing else: a `Resolver` also takes `annotations=`, so `list` would have
    raised the moment a terminal passed it. These tests never reach one, which
    is exactly why the wrong shape survived being written.
    """
    _ = annotations
    return list(keys)


@pytest.fixture
def qy() -> Query[DsdRow, DataStructure]:
    conn = duckdb.connect()
    create_schema(conn)
    conn.executemany(
        "INSERT INTO sdmx.artefact (urn, artefact_type, agency_id, resource_id, version) VALUES (?, ?, ?, ?, ?)",
        [(f"u:{i}", "DataStructure", "ESTAT" if i < 6 else "OECD", f"D{i}", "1.0") for i in range(10)]
        + [("u:cl", "Codelist", "ESTAT", "CL_X", "1.0")],
    )
    return build(q.dsd, conn, _echo_keys)


# ── laziness ──────────────────────────────────────────────────────────────


def test_building_a_query_runs_nothing() -> None:
    """No connection at all, so anything that executed would raise."""
    query: Query[DsdRow, DataStructure] = build(q.dsd, _closed_connection(), _echo_keys)
    query.filter(q.dsd.agency == "ESTAT").limit(5).offset(1)
    assert query.to_sql().sql
    assert query.explain()


def test_a_query_is_not_iterable(qy: Query[DsdRow, DataStructure]) -> None:
    with pytest.raises(TypeError, match=r"not iterable.*\.collect\(\)"):
        iter(qy)


def test_a_query_has_no_len(qy: Query[DsdRow, DataStructure]) -> None:
    with pytest.raises(TypeError, match=r"no len\(\).*\.count\(\)"):
        len(qy)


def test_positional_indexing_is_refused(qy: Query[DsdRow, DataStructure]) -> None:
    """`[]` may refine, it may not extract — polars' rule, and Django's."""
    with pytest.raises(TypeError, match=r"indexed positionally.*first\(\)"):
        qy[0]


def test_a_slice_refines_and_stays_lazy(qy: Query[DsdRow, DataStructure]) -> None:
    sliced = qy[2:5]
    assert isinstance(sliced, Query)
    assert sliced.offset_n == 2
    assert sliced.limit_n == 3


def test_a_slice_step_is_refused(qy: Query[DsdRow, DataStructure]) -> None:
    with pytest.raises(TypeError, match="no step"):
        qy[::2]


# ── the refinement laws ───────────────────────────────────────────────────


def test_filter_ands(qy: Query[DsdRow, DataStructure]) -> None:
    both = qy.filter(q.dsd.agency == "ESTAT").filter(q.dsd.id == "D1")
    one_call = qy.filter(q.dsd.agency == "ESTAT", q.dsd.id == "D1")
    assert both.ids() == ["D1"]
    assert both.ids() == one_call.ids()


def test_filter_order_does_not_matter(qy: Query[DsdRow, DataStructure]) -> None:
    forward = qy.filter(q.dsd.agency == "ESTAT").filter(q.dsd.id == "D1")
    backward = qy.filter(q.dsd.id == "D1").filter(q.dsd.agency == "ESTAT")
    assert forward.ids() == backward.ids()


def test_limit_takes_the_minimum(qy: Query[DsdRow, DataStructure]) -> None:
    """A later caller must not raise an earlier cap."""
    assert qy.limit(3).limit(7).count() == 3
    assert qy.limit(7).limit(3).count() == 3


def test_offset_raises_the_floor_without_sliding_the_window(qy: Query[DsdRow, DataStructure]) -> None:
    """#264: storing `(start, length)` and raising `start` alone slides the
    window past rows an earlier `limit` had excluded."""
    windowed = qy.offset(5).limit(10).offset(8)
    assert windowed.offset_n == 8
    assert windowed.limit_n == 7


def test_limit_and_offset_do_not_commute(qy: Query[DsdRow, DataStructure]) -> None:
    """Asserted rather than merely excluded from a commutativity property — a
    carve-out nobody checks is a place to hide a bug."""
    assert qy.offset(5).limit(10).ids() == ["D5", "D6", "D7", "D8", "D9"]
    assert qy.limit(10).offset(5).ids() == ["D5", "D6", "D7", "D8", "D9"]
    assert qy.limit(3).offset(1).count() == 2
    assert qy.offset(1).limit(3).count() == 3


def test_filtering_after_a_slice_is_refused(qy: Query[DsdRow, DataStructure]) -> None:
    """Both readings are defensible, so the DSL refuses rather than choosing."""
    with pytest.raises(SliceError, match=r"cannot filter\(\).*sliced"):
        qy.limit(3).filter(q.dsd.agency == "ESTAT")
    with pytest.raises(SliceError):
        qy.offset(3).filter(q.dsd.agency == "ESTAT")


def test_slicing_after_a_slice_is_allowed(qy: Query[DsdRow, DataStructure]) -> None:
    """`limit` and `offset` are refinements, and paging is made of them."""
    assert qy.limit(5).limit(2).count() == 2


def test_a_terminal_does_not_consume_the_query(qy: Query[DsdRow, DataStructure]) -> None:
    """`base` stays a plan that can be refined and run again — what makes
    helper-scoped queries safe, and only expressible because a plan is a value.
    """
    base = qy.filter(q.dsd.agency == "ESTAT")
    assert base.count() == 6
    assert base.filter(q.dsd.id == "D1").count() == 1
    assert base.count() == 6


# ── terminals ─────────────────────────────────────────────────────────────


def test_count_ignores_no_rows(qy: Query[DsdRow, DataStructure]) -> None:
    assert qy.count() == 10


def test_count_honours_a_slice(qy: Query[DsdRow, DataStructure]) -> None:
    """#264 again: applying the slice to the aggregate counts every row and
    then limits the one-row result."""
    assert qy.limit(3).count() == 3
    assert qy.offset(8).count() == 2


def test_exists(qy: Query[DsdRow, DataStructure]) -> None:
    assert qy.filter(q.dsd.agency == "ESTAT").exists() is True
    assert qy.filter(q.dsd.agency == "NOBODY").exists() is False


def test_ids_uses_the_relations_own_id_column(qy: Query[DsdRow, DataStructure]) -> None:
    assert qy.ids()[:2] == ["D0", "D1"]


def test_urns_do_not_resolve(qy: Query[DsdRow, DataStructure]) -> None:
    assert qy.limit(2).urns() == ["u:0", "u:1"]


def test_the_root_discriminator_applies(qy: Query[DsdRow, DataStructure]) -> None:
    """A DSD query must not see the codelist in the fixture."""
    assert "CL_X" not in qy.ids()


def test_frame_returns_every_root_column(qy: Query[DsdRow, DataStructure]) -> None:
    frame = qy.limit(2).frame()
    assert frame.height == 2
    assert "resource_id" in frame.columns
    assert "agency_id" in frame.columns


def test_preview_is_bounded(qy: Query[DsdRow, DataStructure]) -> None:
    assert qy.preview(3).height == 3


def test_first_and_one(qy: Query[DsdRow, DataStructure]) -> None:
    """The fixture's resolver is `list`, so a resolved row is its key tuple."""
    assert qy.filter(q.dsd.id == "D1").one() == ("u:1",)
    assert qy.filter(q.dsd.id == "D1").one_or_none() == ("u:1",)
    assert qy.filter(q.dsd.id == "NOPE").one_or_none() is None
    assert qy.filter(q.dsd.id == "NOPE").first() is None
    assert qy.first() == ("u:0",)


def test_one_refuses_anything_but_exactly_one(qy: Query[DsdRow, DataStructure]) -> None:
    with pytest.raises(ValueError, match="expected exactly one row, found 2"):
        qy.one()
    with pytest.raises(ValueError, match="expected exactly one row, found 0"):
        qy.filter(q.dsd.id == "NOPE").one()


def test_one_or_none_refuses_more_than_one(qy: Query[DsdRow, DataStructure]) -> None:
    with pytest.raises(ValueError, match="expected at most one row, found 2"):
        qy.one_or_none()


def test_collect_resolves(qy: Query[DsdRow, DataStructure]) -> None:
    """The expensive terminal — the fixture's resolver is `list`, so this
    checks the wiring rather than the reader.

    A resolver is handed the root's `__key__` columns as tuples, not URNs: not
    every resolvable relation has one, and a reported metadata value is keyed
    by three columns.
    """
    assert qy.limit(2).collect() == [("u:0",), ("u:1",)]


def test_a_query_without_a_resolver_refuses_to_collect() -> None:
    """`build(..., resolve=None)` is how a root says it has no domain object."""
    query: Query[DsdRow, DataStructure] = build(q.dsd, _closed_connection(), None)
    with pytest.raises(TypeError, match=r"nothing knows how to build one.*\.ids\(\)"):
        query.collect()


# ── fan-out through the whole stack ───────────────────────────────────────


def test_a_fan_out_predicate_runs(qy: Query[DsdRow, DataStructure]) -> None:
    conn = duckdb.connect()
    create_schema(conn)
    conn.execute(
        "INSERT INTO sdmx.artefact (urn, artefact_type, agency_id, resource_id, version)"
        " VALUES ('u:dsd', 'DataStructure', 'ESTAT', 'CPI', '1.0')"
    )
    conn.execute(
        "INSERT INTO sdmx.dsd_component (dsd_urn, component_id, component_type) VALUES ('u:dsd', 'FREQ', 'Dimension')"
    )
    query: Query[DsdRow, DataStructure] = build(q.dsd, conn, _echo_keys)
    assert query.filter(q.dsd.dimensions.id == "FREQ").ids() == ["CPI"]
    assert query.filter(q.dsd.dimensions.id == "NOPE").ids() == []
    assert query.filter(q.dsd.dimensions.any(q.component.id == "FREQ")).ids() == ["CPI"]


def test_repr_says_it_is_lazy(qy: Query[DsdRow, DataStructure]) -> None:
    assert "lazy" in repr(qy)
    assert "predicate" in repr(qy.filter(q.dsd.agency == "ESTAT"))
