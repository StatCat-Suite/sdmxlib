"""`order_by` — and the tiebreak nobody asks for.

Two things here are not obvious from the clause itself.

**`order_by` replaces rather than appends**, breaking the refinement principle
every other clause follows. That is deliberate and matches the existing DSLs
(#294): appending cannot be undone, so a helper that sorted would outrank its
caller, and "by A then by B" is one call with two keys.

**Every query is ordered, sorted or not.** An unsorted query still emits the
ROOT's key as a tiebreak, because `limit`/`offset` over an unspecified order
silently skips and repeats rows between calls. That tiebreak is the part a test
has to pin: it is invisible when it works and invisible when it does not.
"""

from __future__ import annotations

import annotationlib
import functools
from typing import TYPE_CHECKING

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import q
from sdmxlib.query.query import SliceError
from sdmxlib.storage.schema import create_schema
from tests.support.repo import ROOT

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Labels and ids that disagree, so a key that is silently ignored shows.

    `CL_AREA`'s codes sort one way by id (`CA`, `US`, `W`) and another by
    English label (`Canada`, `United States`, `World` — the same, deliberately
    not) — so `ZZ`/`Aruba` is included to make label order differ from id
    order. Without that, ordering by the wrong column passes.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_AREA",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Areas"},
                codes=[
                    Code(id="W", name={"en": "World"}),
                    Code(id="CA", name={"en": "Canada"}),
                    Code(id="ZZ", name={"en": "Aruba"}),
                ],
            )
        )
        registry.add(Codelist(id="CL_FREQ", maintainer=Agency(id="OECD"), name={"en": "Frequency"}))
        registry.add(Codelist(id="CL_UNIT", maintainer=Agency(id="ESTAT"), name={"en": "Units"}))
        yield registry


def _codes(reg: sl.LocalRegistry) -> list[str]:
    return reg.query(q.code).ids()


# ── direction, and several keys ───────────────────────────────────────────


def test_ascending_is_the_bare_column(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.code).order_by(q.code.id).ids() == ["CA", "W", "ZZ"]


def test_desc_reverses(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.code).order_by(q.code.id.desc()).ids() == ["ZZ", "W", "CA"]


def test_a_label_is_a_sort_key(reg: sl.LocalRegistry) -> None:
    """Ordering by `name["en"]` must sort by the *subscripted* value, not by
    the whole `MAP` — the same reference bug `_aggregate` had."""
    assert reg.query(q.code).order_by(q.code.name["en"]).ids() == ["ZZ", "CA", "W"]


def test_a_side_table_label_is_a_sort_key(reg: sl.LocalRegistry) -> None:
    """A maintainable's labels are rows in `localized_text`, so this key is a
    correlated scalar subquery inside the `ORDER BY`."""
    assert reg.query(q.codelist).order_by(q.codelist.name["en"]).ids() == [
        "CL_AREA",
        "CL_FREQ",
        "CL_UNIT",
    ]


def test_several_keys_apply_left_to_right(reg: sl.LocalRegistry) -> None:
    """Two codelists share an agency, so the second key decides between them
    and a query that used only the first would not be pinned."""
    found = reg.query(q.codelist).order_by(q.codelist.agency, q.codelist.id.desc()).ids()
    assert found == ["CL_UNIT", "CL_AREA", "CL_FREQ"]


def test_direction_is_per_key(reg: sl.LocalRegistry) -> None:
    """The shape a `descending=[...]` parallel list gets wrong."""
    asc = reg.query(q.codelist).order_by(q.codelist.agency, q.codelist.id).ids()
    desc = reg.query(q.codelist).order_by(q.codelist.agency, q.codelist.id.desc()).ids()
    assert asc == ["CL_AREA", "CL_UNIT", "CL_FREQ"]
    assert desc == ["CL_UNIT", "CL_AREA", "CL_FREQ"]


def test_a_key_may_cross_a_to_one_hop(reg: sl.LocalRegistry) -> None:
    """The join for a sort key has to be planned even when no predicate
    reaches that relation."""
    found = reg.query(q.code).order_by(q.code.codelist.agency, q.code.id).ids()
    assert found == ["CA", "W", "ZZ"]
    assert "ORDER BY" in reg.query(q.code).order_by(q.code.codelist.agency).to_sql().sql


# ── replaces rather than appends ──────────────────────────────────────────


def test_order_by_replaces(reg: sl.LocalRegistry) -> None:
    """Not a refinement, and the one clause here that is not."""
    once = reg.query(q.code).order_by(q.code.id.desc())
    assert once.order_by(q.code.id).ids() == ["CA", "W", "ZZ"]


def test_no_arguments_clears(reg: sl.LocalRegistry) -> None:
    sorted_desc = reg.query(q.code).order_by(q.code.id.desc())
    assert sorted_desc.order_by().ids() == _codes(reg)


def test_order_by_after_a_slice_is_refused(reg: sl.LocalRegistry) -> None:
    """A different order over the same window is a different set of rows, so
    this is the violation `filter` after `limit` is."""
    with pytest.raises(SliceError, match=r"cannot order_by\(\).*sliced"):
        reg.query(q.code).limit(2).order_by(q.code.id)
    with pytest.raises(SliceError):
        reg.query(q.code).offset(1).order_by(q.code.id)


def test_slicing_after_ordering_is_allowed(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.code).order_by(q.code.id.desc()).limit(2).ids() == ["ZZ", "W"]
    assert reg.query(q.code).order_by(q.code.id).offset(1).limit(1).ids() == ["W"]


# ── the tiebreak ──────────────────────────────────────────────────────────


def test_an_unsorted_query_is_still_ordered(reg: sl.LocalRegistry) -> None:
    """No caller keys, and the clause is emitted anyway."""
    compiled = reg.query(q.code).to_sql()
    assert "ORDER BY s0.code_urn NULLS LAST" in compiled.sql


def test_the_tiebreak_follows_the_caller_keys(reg: sl.LocalRegistry) -> None:
    sql = reg.query(q.code).order_by(q.code.id.desc()).to_sql().sql
    assert sql.endswith("ORDER BY s0.code_id DESC NULLS LAST, s0.code_urn NULLS LAST")


def test_the_tiebreak_decides_rows_the_keys_tie_on(reg: sl.LocalRegistry) -> None:
    """Every code here has the same codelist, so that key ties on all three and
    only the tiebreak makes the answer repeatable."""
    by_parent = reg.query(q.code).order_by(q.code.codelist.id)
    assert by_parent.ids() == by_parent.ids()
    assert by_parent.ids() == sorted(by_parent.ids(), key=lambda i: {"CA": 0, "W": 1, "ZZ": 2}[i])


def test_paging_over_an_unsorted_query_covers_every_row(reg: sl.LocalRegistry) -> None:
    """What the tiebreak is for. Without a total order two pages can overlap
    and miss, and the failure is intermittent rather than reproducible."""
    base = reg.query(q.code)
    pages = [base[0:2].ids(), base[2:4].ids()]
    assert sorted(p for page in pages for p in page) == ["CA", "W", "ZZ"]


def test_nulls_are_placed_explicitly(reg: sl.LocalRegistry) -> None:
    """`default_null_order` is a session setting, and the backends §4 adds do
    not agree. Saying it costs nothing."""
    sql = reg.query(q.code).order_by(q.code.parent.id, q.code.id.desc()).to_sql().sql
    assert "s0.parent_id NULLS LAST" in sql
    assert "s0.code_id DESC NULLS LAST" in sql


def test_nulls_sort_last_in_both_directions(reg: sl.LocalRegistry) -> None:
    """Every code in the fixture has a null `parent_id`, so add one that does
    not and check the null does not lead."""
    with sl.LocalRegistry(":memory:") as reg2:
        reg2.add(
            Codelist(
                id="CL",
                maintainer=Agency(id="E"),
                name={"en": "x"},
                codes=[Code(id="ROOT", name={"en": "r"}), Code(id="KID", name={"en": "k"}, parent_id="ROOT")],
            )
        )
        assert reg2.query(q.code).order_by(q.code.parent.id).ids() == ["KID", "ROOT"]
        assert reg2.query(q.code).order_by(q.code.parent.id.desc()).ids() == ["KID", "ROOT"]


# ── refusals ──────────────────────────────────────────────────────────────


def test_ordering_by_a_fan_out_is_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(PathError, match=r"cannot order by.*to-many"):
        reg.query(q.codelist).order_by(q.codelist.codes.id).ids()


def test_ordering_by_a_predicate_is_refused(reg: sl.LocalRegistry) -> None:
    """`order_by(q.code.id == "A")` is the plausible slip — #266 in the
    existing DSLs, and the message names the fix."""
    with pytest.raises(TypeError, match=r"takes a column.*which rows.*\.desc\(\)"):
        reg.query(q.code).order_by(q.code.id == "A")  # pyrefly: ignore[bad-argument-type]


def test_ordering_by_an_aggregate_is_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(TypeError, match="takes a column, got Agg"):
        reg.query(q.codelist).order_by(q.codelist.codes.id.list())  # pyrefly: ignore[bad-argument-type]


# ── it reaches the other surfaces ─────────────────────────────────────────


def test_a_projection_inherits_the_order(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.code).order_by(q.code.id.desc()).select(code=q.code.id).collect()
    assert frame["code"].to_list() == ["ZZ", "W", "CA"]


def test_a_projection_may_sort_by_something_it_does_not_select(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.code).order_by(q.code.name["en"]).select(code=q.code.id).collect()
    assert frame["code"].to_list() == ["ZZ", "CA", "W"]


def test_frame_and_urns_are_ordered(reg: sl.LocalRegistry) -> None:
    ordered = reg.query(q.code).order_by(q.code.id.desc())
    assert ordered.frame()["code_id"].to_list() == ["ZZ", "W", "CA"]
    assert [u.rsplit(".", 1)[-1] for u in ordered.urns()] == ["ZZ", "W", "CA"]


def test_collect_is_ordered(reg: sl.LocalRegistry) -> None:
    ordered = reg.query(q.codelist).order_by(q.codelist.id.desc())
    assert [c.id for c in ordered.collect()] == ["CL_UNIT", "CL_FREQ", "CL_AREA"]


def test_first_takes_the_first_in_order(reg: sl.LocalRegistry) -> None:
    """`first()` is only meaningful against a stated order, and is the terminal
    most likely to be read as "any"."""
    found = reg.query(q.codelist).order_by(q.codelist.id.desc()).first()
    assert found is not None
    assert found.id == "CL_UNIT"


def test_count_and_exists_drop_the_order(reg: sl.LocalRegistry) -> None:
    """`count(*)` sorts nothing, and a key naming a column outside the
    aggregate is a binder error rather than a wasted clause."""
    ordered = reg.query(q.code).order_by(q.code.name["en"])
    assert "ORDER BY" not in ordered.to_sql(columns="count(*)", ordered=False).sql
    assert ordered.count() == 3
    assert ordered.limit(2).count() == 2
    assert ordered.exists() is True


def test_the_query_stays_lazy(reg: sl.LocalRegistry) -> None:
    ordered = reg.query(q.code).order_by(q.code.id)
    assert "sort key" in repr(ordered)
    assert ordered.explain()


# ── the tiebreak is a real key ────────────────────────────────────────────


@functools.cache
def _declared_keys() -> dict[str, set[tuple[str, ...]]]:
    """Every declared uniqueness per table, from constraints and unique indexes.

    A set of tuples rather than one key: `sdmx.code` is keyed by
    `(codelist_urn, code_id)` *and* carries a unique index on `code_urn`, and
    either is a sound tiebreak.
    """
    conn = duckdb.connect()
    create_schema(conn)
    out: dict[str, set[tuple[str, ...]]] = {}
    for table, columns in conn.execute(
        """
        SELECT table_name, constraint_column_names
        FROM duckdb_constraints()
        WHERE schema_name = 'sdmx' AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        """
    ).fetchall():
        out.setdefault(str(table), set()).add(tuple(str(c) for c in columns))
    for table, sql in conn.execute(
        "SELECT table_name, sql FROM duckdb_indexes() WHERE schema_name = 'sdmx' AND is_unique"
    ).fetchall():
        inner = str(sql).rsplit("(", 1)[-1].rstrip(");").strip()
        out.setdefault(str(table), set()).add(tuple(c.strip().strip('"') for c in inner.split(",")))
    return out


#: Roots whose relation is a *view*, and the test that proves each tiebreak
#: unique instead.
#:
#: DuckDB cannot declare a constraint on a view, so `_declared_keys` has
#: nothing to read — not because the key is doubtful but because the place it
#: would be written does not exist. An exemption alone would be the wrong
#: trade: the check exists because a non-unique tiebreak *looks* present while
#: doing nothing. So each entry names a test that demonstrates uniqueness on
#: data built to violate it, and `test_every_view_tiebreak_names_a_live_proof`
#: keeps the pointer from rotting.
VIEW_TIEBREAKS: dict[str, tuple[str, str]] = {
    "hierarchy_applicability": (
        "tests/unit/test_query_applicability.py",
        "test_the_tiebreak_is_unique_on_data_built_to_break_it",
    ),
}


def test_every_view_tiebreak_names_a_live_proof() -> None:
    """A pointer to a test that no longer exists is worse than no pointer."""
    for table, (path, name) in VIEW_TIEBREAKS.items():
        proof = ROOT / path
        assert proof.is_file(), f"{table}: no such file {path}"
        assert f"def {name}(" in proof.read_text(), f"{table}: {path} has no {name}"


@pytest.mark.parametrize("name", sorted(annotationlib.get_annotations(type(q), format=annotationlib.Format.STRING)))
def test_the_tiebreak_is_a_declared_key(name: str) -> None:
    """A tiebreak that is not unique does not tie-break, and nothing else in
    the suite would say so — two rows equal on it come back in scan order, so
    the clause looks present and is not doing its job.

    Checked against the live constraints rather than by eye, in the same shape
    `test_query_edges.py` checks cardinality — and parametrised off `sl.q`
    rather than a hand-listed set, because a new ROOT is exactly when this is
    worth having. It was a hand-listed set until the metadata roots landed and
    it silently did not cover them.
    """
    accessor = type(getattr(q, name))
    table = accessor.__table__
    if table in VIEW_TIEBREAKS:
        return
    assert accessor.__key__ in _declared_keys()[table], (
        f"q.{name} orders by {accessor.__key__} as a tiebreak, but {table} "
        f"declares no such uniqueness — its keys are {sorted(_declared_keys()[table])}"
    )


def test_every_root_declares_a_tiebreak() -> None:
    """Inheriting `("urn",)` is right for a maintainable and wrong for anything
    else, so a new ROOT with its own table must not silently take it."""
    for name in annotationlib.get_annotations(type(q), format=annotationlib.Format.STRING):
        accessor = type(getattr(q, name))
        if accessor.__table__ in VIEW_TIEBREAKS:
            assert accessor.__key__ != ("urn",), f"{name}: a view ROOT still inherits the maintainable tiebreak"
            continue
        assert accessor.__key__ in _declared_keys()[accessor.__table__], name


def test_a_composite_key_emits_every_column() -> None:
    """`dsd_component` has no single unique column, so its tiebreak is two."""
    sql = str(
        __import__("sdmxlib.query._compile", fromlist=["compile_select"]).compile_select(q.component, order=()).sql
    )
    assert sql.endswith("ORDER BY s0.dsd_urn NULLS LAST, s0.component_id NULLS LAST")
