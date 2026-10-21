"""Computed columns — arithmetic and concatenation, evaluated by the database.

The point is not the operators. It is that a computed column is *still a
column*: projectable, sortable, comparable, windowable, and composable with
more arithmetic — so nothing downstream had to learn about it.

`Num` and `Str` carry different operators, and that is the whole reason the two
types exist rather than one `Col`. `+` means addition on one and concatenation
on the other, exactly as in Python, and the operand types do not overlap — so
`q.code.position + "x"` and `q.code.id + 1` are both rejected by the checker
rather than producing a surprise at the database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query._expr import Col
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import CodeRow, q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Ids of differing length and a hierarchy, so `len()` and `position`
    both discriminate."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_AREA",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Areas"},
                codes=[
                    Code(id="W", name={"en": "World"}),
                    Code(id="CA", name={"en": "Canada"}, parent_id="W"),
                    Code(id="USA", name={"en": "United States"}, parent_id="W"),
                ],
            )
        )
        yield registry


def _values(reg: sl.LocalRegistry, **columns: Col[CodeRow]) -> dict[str, object]:
    """One projected row per code, keyed by code id."""
    frame = reg.query(q.code).select(code=q.code.id, **columns).collect()
    name = next(iter(columns))
    return dict(zip(frame["code"].to_list(), frame[name].to_list(), strict=True))


# ── numeric ───────────────────────────────────────────────────────────────


def test_addition(reg: sl.LocalRegistry) -> None:
    assert _values(reg, x=q.code.position + 1) == {"W": 1, "CA": 2, "USA": 3}


def test_subtraction_multiplication_and_division(reg: sl.LocalRegistry) -> None:
    assert _values(reg, x=q.code.position - 1) == {"W": -1, "CA": 0, "USA": 1}
    assert _values(reg, x=q.code.position * 10) == {"W": 0, "CA": 10, "USA": 20}
    assert _values(reg, x=(q.code.position + 2) / 2) == {"W": 1.0, "CA": 1.5, "USA": 2.0}


def test_the_reflected_operators(reg: sl.LocalRegistry) -> None:
    """`1 + col` as well as `col + 1`, and `-` and `/` are not commutative — so
    a missing `__rsub__` would silently give the wrong sign."""
    assert _values(reg, x=1 + q.code.position) == {"W": 1, "CA": 2, "USA": 3}
    assert _values(reg, x=10 - q.code.position) == {"W": 10, "CA": 9, "USA": 8}
    assert _values(reg, x=2 * q.code.position) == {"W": 0, "CA": 2, "USA": 4}
    assert _values(reg, x=4 / (q.code.position + 1)) == {"W": 4.0, "CA": 2.0, "USA": 4 / 3}


def test_negation(reg: sl.LocalRegistry) -> None:
    assert _values(reg, x=-q.code.position) == {"W": 0, "CA": -1, "USA": -2}


def test_arithmetic_composes(reg: sl.LocalRegistry) -> None:
    """A computed column is a `Num`, so it takes more arithmetic — which is
    what "still a column" has to mean to be worth anything."""
    assert _values(reg, x=(q.code.position + 1) * 10 - 5) == {"W": 5, "CA": 15, "USA": 25}


def test_division_by_zero_is_inf_not_an_error(reg: sl.LocalRegistry) -> None:
    """DuckDB divides as IEEE-754 floats: `1/0` is `inf`, not `NULL` and not a
    raise. Left alone rather than wrapped, so the answer is the database's."""
    values = _values(reg, x=1 / q.code.position)
    assert values["W"] == float("inf")
    assert values["CA"] == 1.0
    assert values["USA"] == 0.5


# ── text ──────────────────────────────────────────────────────────────────


def test_concatenation(reg: sl.LocalRegistry) -> None:
    assert _values(reg, x=q.code.id + "!") == {"W": "W!", "CA": "CA!", "USA": "USA!"}


def test_concatenation_across_a_hop(reg: sl.LocalRegistry) -> None:
    """Two operands on *different* relations, which is why rendering a
    computed column lives on the scope: each needs its own alias."""
    assert _values(reg, x=q.code.codelist.id + ":" + q.code.id) == {
        "W": "CL_AREA:W",
        "CA": "CL_AREA:CA",
        "USA": "CL_AREA:USA",
    }


def test_a_leading_literal_concatenates(reg: sl.LocalRegistry) -> None:
    assert _values(reg, x="code:" + q.code.id) == {"W": "code:W", "CA": "code:CA", "USA": "code:USA"}


def test_concatenating_a_label(reg: sl.LocalRegistry) -> None:
    """A localised label is a subscripted `MAP`, so this checks the operand is
    rendered through the same reference rule rather than as a bare column."""
    assert _values(reg, x=q.code.id + "=" + q.code.name["en"]) == {
        "W": "W=World",
        "CA": "CA=Canada",
        "USA": "USA=United States",
    }


def test_concatenating_null_is_null(reg: sl.LocalRegistry) -> None:
    """SQL's rule, not Python's. Stated in the docstring and asserted here
    because the two disagree and the DSL follows SQL."""
    assert _values(reg, x=q.code.parent.id + "!") == {"W": None, "CA": "W!", "USA": "W!"}


def test_len(reg: sl.LocalRegistry) -> None:
    assert _values(reg, x=q.code.id.len()) == {"W": 1, "CA": 2, "USA": 3}


def test_len_of_a_concatenation(reg: sl.LocalRegistry) -> None:
    """`Str.len()` returns a `Num`, and `StrExpr` is a `Str` — so the two
    compose in both directions."""
    assert _values(reg, x=(q.code.id + "--").len() + 1) == {"W": 4, "CA": 5, "USA": 6}


def test_a_computed_string_keeps_the_str_namespace(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.code).filter((q.code.codelist.id + ":" + q.code.id).str.contains("AREA:CA"))
    assert found.ids() == ["CA"]


# ── still a column ────────────────────────────────────────────────────────


def test_a_computed_column_filters(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.code).filter(q.code.id.len() == 3).ids() == ["USA"]
    assert sorted(reg.query(q.code).filter(q.code.position + 1 >= 2).ids()) == ["CA", "USA"]


def test_a_computed_column_sorts(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.code).order_by(q.code.id.len()).ids() == ["W", "CA", "USA"]
    assert reg.query(q.code).order_by((q.code.id.len()).desc()).ids() == ["USA", "CA", "W"]


def test_a_computed_column_pages(reg: sl.LocalRegistry) -> None:
    """The cursor compares the computed expression, not a stored column."""
    ordered = reg.query(q.code).order_by(q.code.id.len())
    page = ordered.page(2)
    assert page.cursor is not None
    assert page.urns + ordered.after(page.cursor).page(2).urns == tuple(ordered.urns())


def test_a_computed_column_partitions_a_window(reg: sl.LocalRegistry) -> None:
    longest = q.code.position.rank(descending=True).over(q.code.id.len()) == 1
    assert sorted(reg.query(q.code).qualify(longest).ids()) == ["CA", "USA", "W"]


def test_a_computed_column_is_collected_by_an_aggregate(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.codelist).select(cl=q.codelist.id, lengths=q.codelist.codes.id.len().list()).collect()
    assert sorted(frame["lengths"].to_list()[0]) == [1, 2, 3]


def test_a_computed_column_is_uniqued(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.code).select(n=q.code.id.len() - q.code.id.len()).unique().collect()
    assert frame["n"].to_list() == [0]


# ── refusals ──────────────────────────────────────────────────────────────


def test_computing_across_a_fan_out_is_refused(reg: sl.LocalRegistry) -> None:
    """Every operand is checked, not just the first — the fan-out is on the
    second one here."""
    with pytest.raises(PathError, match=r"in a computed column.*to-many"):
        reg.query(q.codelist).select(x=q.codelist.id + q.codelist.codes.id).collect()


def test_filtering_on_a_fan_out_computation_still_means_some_child(reg: sl.LocalRegistry) -> None:
    """A predicate is not a projection: dotting through a fan-out in a filter
    has always meant *some* child, and arithmetic does not change that."""
    assert reg.query(q.codelist).filter(q.codelist.codes.id.len() == 3).ids() == ["CL_AREA"]
    assert reg.query(q.codelist).filter(q.codelist.codes.id.len() == 9).ids() == []


def test_values_are_bound_not_interpolated(reg: sl.LocalRegistry) -> None:
    """A literal operand is a bind like any other value in this compiler."""
    compiled = reg.query(q.code).select(x=q.code.id + "'; DROP TABLE sdmx.code; --").to_sql()
    assert "DROP TABLE" not in compiled.sql
    assert "'; DROP TABLE sdmx.code; --" in compiled.params
