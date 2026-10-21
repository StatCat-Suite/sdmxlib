"""Set operations and `unique()` — where the refinement principle splits.

Measured against polars' equivalents, the three set operations do not behave
alike: `intersect` and `difference` always return a subset of their left
argument, and `union` does not. So the first two are methods and **`union` is a
free function**, because `helper_scoped_to_estat.union(everything)` would undo
the helper's scoping — precisely what the principle exists to prevent.

`unique()` is full-row only for a different reason: the subset form fails to
commute with `filter` *and* leaves which row survives unspecified. Excluding it
keeps `unique` in the commuting set rather than adding a carve-out to the law.

The two share an implementation detail worth naming. `intersect`/`difference`
compile to a correlated `EXISTS` / `NOT EXISTS` on the *left* query, so they are
refinements structurally and not merely by promise — everything downstream sees
one more `WHERE` and behaves exactly as it did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency
from sdmxlib.query import union
from sdmxlib.query._compile import ROOT_ALIAS
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import CodelistRow, q
from sdmxlib.query.query import Query, SliceError
from sdmxlib.sql import SqlRelation
from sdmxlib.storage.lazy import LazyCodelist
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Four codelists across two agencies, so every operation has a non-empty
    and a non-total answer — the two ways a set operation can look right while
    being the wrong one."""
    with sl.LocalRegistry(":memory:") as registry:
        for cid, agency in [("CL_A", "ESTAT"), ("CL_B", "OECD"), ("CL_C", "ESTAT"), ("CL_D", "OECD")]:
            registry.add(Codelist(id=cid, maintainer=Agency(id=agency), name={"en": cid}))
        registry.add(
            DataStructure(
                id="CPI",
                maintainer=Agency(id="ESTAT"),
                name={"en": "CPI"},
                dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"))],
            )
        )
        yield registry


def _estat(reg: sl.LocalRegistry) -> Query[CodelistRow, LazyCodelist]:
    return reg.query(q.codelist).filter(q.codelist.agency == "ESTAT")


def _bc(reg: sl.LocalRegistry) -> Query[CodelistRow, LazyCodelist]:
    return reg.query(q.codelist).filter(q.codelist.id.is_in(["CL_B", "CL_C"]))


# ── the three operations ──────────────────────────────────────────────────


def test_the_operands_are_what_the_fixture_says(reg: sl.LocalRegistry) -> None:
    """Stated once so every assertion below reads as arithmetic."""
    assert sorted(_estat(reg).ids()) == ["CL_A", "CL_C"]
    assert sorted(_bc(reg).ids()) == ["CL_B", "CL_C"]


def test_intersect_is_a_semi_join(reg: sl.LocalRegistry) -> None:
    assert _estat(reg).intersect(_bc(reg)).ids() == ["CL_C"]


def test_difference_is_an_anti_join(reg: sl.LocalRegistry) -> None:
    assert _estat(reg).difference(_bc(reg)).ids() == ["CL_A"]


def test_union_takes_rows_the_left_query_excluded(reg: sl.LocalRegistry) -> None:
    """What no method could express: `CL_B` is not in `estat` at all."""
    assert sorted(union(_estat(reg), _bc(reg)).ids()) == ["CL_A", "CL_B", "CL_C"]


def test_intersect_and_difference_narrow(reg: sl.LocalRegistry) -> None:
    """The measurement that decided which of the three may be a method."""
    base = set(_estat(reg).ids())
    assert set(_estat(reg).intersect(_bc(reg)).ids()) <= base
    assert set(_estat(reg).difference(_bc(reg)).ids()) <= base
    assert not set(union(_estat(reg), _bc(reg)).ids()) <= base


def test_the_two_halves_partition_the_left_query(reg: sl.LocalRegistry) -> None:
    """`intersect` and `difference` against the same operand are complements —
    a law neither test above would catch if both were subtly wrong."""
    kept = _estat(reg).intersect(_bc(reg)).ids()
    dropped = _estat(reg).difference(_bc(reg)).ids()
    assert sorted([*kept, *dropped]) == sorted(_estat(reg).ids())
    assert not set(kept) & set(dropped)


def test_union_of_one_is_that_query(reg: sl.LocalRegistry) -> None:
    assert union(_estat(reg)).ids() == _estat(reg).ids()


def test_union_takes_more_than_two(reg: sl.LocalRegistry) -> None:
    d = reg.query(q.codelist).filter(q.codelist.id == "CL_D")
    assert sorted(union(_estat(reg), _bc(reg), d).ids()) == ["CL_A", "CL_B", "CL_C", "CL_D"]


def test_union_does_not_duplicate_a_shared_row(reg: sl.LocalRegistry) -> None:
    """`CL_C` is in both operands. Rows of a keyed relation cannot repeat, so
    the union is a set union and not a concatenation."""
    assert union(_estat(reg), _bc(reg)).ids().count("CL_C") == 1


# ── they are ordinary queries afterwards ──────────────────────────────────


def test_a_set_operation_composes_with_everything_downstream(reg: sl.LocalRegistry) -> None:
    combined = union(_estat(reg), _bc(reg))
    assert combined.order_by(q.codelist.id.desc()).ids() == ["CL_C", "CL_B", "CL_A"]
    assert combined.count() == 3
    assert isinstance(combined.limit(2), Query)  # still a query
    assert combined.filter(q.codelist.agency == "OECD").ids() == ["CL_B"]


def test_the_left_querys_own_clauses_survive_an_intersect(reg: sl.LocalRegistry) -> None:
    """It is one more `WHERE` on the same statement, so the order still holds."""
    found = _estat(reg).order_by(q.codelist.id.desc()).intersect(reg.query(q.codelist))
    assert found.ids() == ["CL_C", "CL_A"]


def test_an_operands_slice_is_honoured(reg: sl.LocalRegistry) -> None:
    """The right-hand query is compiled whole — predicates, order and slice.

    Sorted *descending* on purpose. Ascending would agree with the scan order,
    so a subquery that dropped its `ORDER BY` and took an arbitrary row would
    still answer `CL_A` and the test would prove nothing.
    """
    last = reg.query(q.codelist).order_by(q.codelist.id.desc()).limit(1)
    assert reg.query(q.codelist).intersect(last).ids() == ["CL_D"]
    first = reg.query(q.codelist).order_by(q.codelist.id).limit(1)
    assert reg.query(q.codelist).intersect(first).ids() == ["CL_A"]


def test_a_set_operation_after_a_slice_is_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(SliceError, match=r"cannot intersect\(\)"):
        reg.query(q.codelist).limit(2).intersect(_bc(reg))
    with pytest.raises(SliceError, match=r"cannot difference\(\)"):
        reg.query(q.codelist).limit(2).difference(_bc(reg))


def test_a_set_operation_stays_lazy(reg: sl.LocalRegistry) -> None:
    combined = union(_estat(reg), _bc(reg))
    assert "lazy" in repr(combined)
    assert "EXISTS" in combined.to_sql().sql
    assert "UNION" in combined.to_sql().sql


def test_a_composite_key_correlates_on_every_column(reg: sl.LocalRegistry) -> None:
    """`dsd_component` has no single unique column, so the correlation is two
    equalities — one would match the wrong rows across DSDs."""
    sql = reg.query(q.component).intersect(reg.query(q.component)).to_sql().sql
    assert "dsd_urn = s0.dsd_urn" in sql
    assert "component_id = s0.component_id" in sql


# ── mixing relations ──────────────────────────────────────────────────────


def test_mixing_relations_is_refused(reg: sl.LocalRegistry) -> None:
    """The row kind rejects most of this statically. `q.dsd` and `q.codelist`
    are the *same* relation differing only by discriminator, so their keys line
    up and the answer would be silently empty rather than wrong-looking."""
    # Suppressed because the static rejection is the *subject*: this asserts
    # the runtime backstop for the case the row kind cannot separate, and the
    # call has to be written to be refused. Same shape as `test_query_misuse`.
    with pytest.raises(TypeError, match="cannot combine a query over"):
        reg.query(q.codelist).intersect(reg.query(q.dsd))  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="cannot combine a query over"):
        union(reg.query(q.codelist), reg.query(q.dsd))  # pyrefly: ignore[bad-argument-type]


def test_two_queries_over_the_same_root_are_fine(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.codelist).intersect(reg.query(q.codelist)).count() == 4


# ── unique ────────────────────────────────────────────────────────────────


def test_unique_collapses_duplicates(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(q.codelist.agency).unique().collect()
    assert frame["agency"].to_list() == ["ESTAT", "OECD"]


def test_without_unique_the_duplicates_stay(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(q.codelist.agency).collect()
    assert frame.height == 4


def test_unique_takes_no_arguments(reg: sl.LocalRegistry) -> None:
    """The subset form does not commute with `filter` and leaves which row
    survives unspecified, so it is excluded rather than given a precondition."""
    with pytest.raises(TypeError):
        reg.query(q.codelist).select(q.codelist.agency).unique(q.codelist.agency)  # pyrefly: ignore[bad-argument-count]


def test_unique_is_ordered_without_being_asked(reg: sl.LocalRegistry) -> None:
    """The root key is gone after the collapse, so the tiebreak becomes the
    projection's ordinals — leaving it in scan order would reintroduce exactly
    the instability the default tiebreak removes."""
    frame = reg.query(q.codelist).select(q.codelist.agency).unique()
    assert frame.to_sql().sql.endswith("ORDER BY 1 NULLS LAST")
    assert frame.collect()["agency"].to_list() == sorted({"ESTAT", "OECD"})


def test_unique_drops_its_tiebreak_when_the_order_is_declined(reg: sl.LocalRegistry) -> None:
    """`order_by(None)` reaches the `DISTINCT` tiebreak too (#389).

    The clause above exists so a collapsed result is stable rather than merely
    unspecified; this is the caller saying they do not need that, and a
    stability clause nobody asked for is exactly what #389 measured the cost
    of. The rows are the same set either way.
    """
    frame = reg.query(q.codelist).order_by(None).select(q.codelist.agency).unique()
    assert "ORDER BY" not in frame.to_sql().sql
    assert set(frame.collect()["agency"].to_list()) == {"ESTAT", "OECD"}


def test_unique_honours_a_caller_order(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).order_by(q.codelist.agency.desc()).select(q.codelist.agency).unique().collect()
    assert frame["agency"].to_list() == ["OECD", "ESTAT"]


def test_ordering_a_unique_projection_by_something_unprojected_is_refused(reg: sl.LocalRegistry) -> None:
    """SQL's own rule, and a real one: after the duplicates collapse there is
    no single value of the dropped column left to sort on."""
    with pytest.raises(PathError, match=r"cannot order a unique\(\) projection by 'resource_id'"):
        reg.query(q.codelist).order_by(q.codelist.id).select(q.codelist.agency).unique().collect()


def test_unique_commutes_with_filter(reg: sl.LocalRegistry) -> None:
    """The property that excluding the subset form buys, asserted rather than
    assumed — `unique(agency)` is where it stops holding."""
    filtered = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT")
    assert filtered.select(q.codelist.agency).unique().collect().to_dicts() == [{"agency": "ESTAT"}]


def test_unique_over_several_columns(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(q.codelist.agency, q.codelist.version).unique().collect()
    assert frame.height == 2
    assert frame.to_dicts()[0]["agency"] == "ESTAT"


def test_unique_shows_in_the_repr(reg: sl.LocalRegistry) -> None:
    assert "unique" in repr(reg.query(q.codelist).select(q.codelist.agency).unique())
    assert "unique" not in repr(reg.query(q.codelist).select(q.codelist.agency))


def test_unique_skips_aggregates_in_its_tiebreak(reg: sl.LocalRegistry) -> None:
    """Ordering by a collected list is meaningless, so the ordinal tiebreak
    covers the plain columns only."""
    frame = reg.query(q.codelist).select(cl=q.codelist.id, codes=q.codelist.codes.id.list()).unique()
    assert frame.to_sql().sql.endswith("ORDER BY 1 NULLS LAST")
    assert frame.collect().height == 4


def test_a_code_fixture_is_not_needed_for_the_above(reg: sl.LocalRegistry) -> None:
    """Guard on the fixture: the aggregate test would pass vacuously if every
    codelist were empty *and* the projection were broken, so pin that codes
    exist somewhere."""
    reg.add(
        Codelist(id="CL_E", maintainer=Agency(id="ESTAT"), name={"en": "E"}, codes=[Code(id="X", name={"en": "x"})])
    )
    frame = reg.query(q.codelist).filter(q.codelist.id == "CL_E").select(codes=q.codelist.codes.id.list()).collect()
    assert frame["codes"].to_list()[0] == ["X"]


def test_union_of_sliced_operands(reg: sl.LocalRegistry) -> None:
    """ "The first of each agency" — two windows that no single query states.

    A branch carrying its own `ORDER BY` and `LIMIT` has to be parenthesised:
    `SELECT ... LIMIT 1 UNION SELECT ...` is a parse error, not a different
    reading. `intersect` never meets it — one branch, no `UNION` to bind
    tighter than — which is why every union test until now used whole
    operands.
    """
    first_estat = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").order_by(q.codelist.id).limit(1)
    first_oecd = reg.query(q.codelist).filter(q.codelist.agency == "OECD").order_by(q.codelist.id).limit(1)
    assert sorted(union(first_estat, first_oecd).ids()) == ["CL_A", "CL_B"]


# ── a query is a relation ─────────────────────────────────────────────────


def test_a_projection_is_a_sql_relation(reg: sl.LocalRegistry) -> None:
    """`sdmxlib.sql.SqlRelation` is the seam a lazy single-column relation
    splices through, and other libraries implement it too. Conforming
    structurally — rather than through an adapter — is what lets a
    new-language query go wherever a `CodeQuery` already goes.
    """
    projection = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").select(q.codelist.urn)
    assert isinstance(projection, SqlRelation)
    assert projection.columns == ("urn",)

    # Named in *projection* order, not sorted: the protocol's contract is
    # "names of the columns `to_sql` produces, in order", and a consumer
    # zipping names to values would misalign otherwise.
    wide = reg.query(q.codelist).select(zed=q.codelist.id, alpha=q.codelist.agency)
    assert wide.columns == ("zed", "alpha")
    assert wide.collect().columns == ["zed", "alpha"]
    sql, params = projection.to_sql()
    assert sql.startswith("SELECT")
    assert "ESTAT" in params


def test_the_compiled_pair_is_both_a_tuple_and_a_record(reg: sl.LocalRegistry) -> None:
    """A `NamedTuple`, so the 50-odd `compiled.sql` sites and the protocol's
    `sql, params = ...` both work. Either alone would have meant an adapter."""
    compiled = reg.query(q.codelist).to_sql()
    sql, params = compiled
    assert sql == compiled.sql
    assert params == compiled.params
    assert isinstance(params, list), "the protocol says list, and a tuple does not satisfy it"


def test_a_whole_query_is_a_relation_but_not_a_single_column_one(reg: sl.LocalRegistry) -> None:
    """`to_sql()` with no argument selects every root column, so a bare query
    is a multi-column relation. `is_in` says so by name rather than letting the
    database emit a column-count error (#274) — and before this it raised
    `AttributeError: no attribute 'columns'`, which named nothing.
    """
    whole = reg.query(q.codelist)
    assert isinstance(whole, SqlRelation)
    # Exact, not `> 1`: this is the precondition for the refusal below, and a
    # bound would still hold if the relation silently narrowed to two (#403).
    assert len(whole.columns) == 18, f"q.codelist projects eighteen columns; got {whole.columns}"
    with pytest.raises(TypeError, match=r"one-column relation, got \d+"):
        q.codelist.urn.is_in(whole)


def test_one_query_filters_another(reg: sl.LocalRegistry) -> None:
    """The shape the peers use for "matched by either route": a sub-query as a
    membership predicate, which composes where a set operation cannot."""
    estat = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").select(q.codelist.urn)
    oecd = reg.query(q.codelist).filter(q.codelist.agency == "OECD").select(q.codelist.urn)
    either = q.codelist.urn.is_in(estat) | q.codelist.urn.is_in(oecd)
    assert sorted(reg.query(q.codelist).filter(either).ids()) == ["CL_A", "CL_B", "CL_C", "CL_D"]
    # ...and because it is a predicate, it narrows further and negates.
    assert reg.query(q.codelist).filter(either, q.codelist.id == "CL_B").ids() == ["CL_B"]
    assert reg.query(q.codelist).filter(~either).ids() == []


def test_the_root_alias_is_a_constant(reg: sl.LocalRegistry) -> None:
    """`_alias` used to compile a throwaway query to read this back, on every
    `ids()` call. The outer scope is always allocated first, so it is a fact
    about the compiler — asserted here so it stays one."""
    assert ROOT_ALIAS == "s0"
    for query in (reg.query(q.codelist), reg.query(q.code).filter(q.code.codelist.id == "CL_A")):
        assert f"{ROOT_ALIAS}." in query.to_sql().sql
