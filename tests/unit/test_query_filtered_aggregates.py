"""`where=` on an aggregate — collect some of the fan-out, not all of it (#373).

Before this every aggregate was unconditional: `list()` collected the whole
fan-out or nothing, and "the dimensions of each DSD" had no spelling. `filter`
could not say it — that selects which *rows* come back, not which children are
collected — and rooting at the child gives the rows while losing the grouping.

The shape is a keyword rather than a chained `.where(p)` because a filtered
fan-out object would immediately duplicate what exists: `fan.where(p).any()`
is `fan.any(p)`. Predicates already go *inside* the operation here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import Attribute, DataStructure, Dimension, Measure
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref
from tests.support.strategies import annotated_forest_codelists

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

#: Property width, matching the other query property modules.
_EXAMPLES = 50

_NAME = {"en": "x"}
_C = q.dsd.components


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """A DSD with all three component roles, and a codelist with annotations.

    Every fixture value is chosen so a restriction has something to *exclude*:
    four components across three roles, and codes carrying two annotation
    types, so a filtered aggregate that quietly ignored `where=` would return
    a visibly different answer.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_GEO",
                maintainer="SC",
                version="1.0",
                name=_NAME,
                codes=[
                    Code(id="CA", name=_NAME, annotations=[Annotation(id="a1", type="note", text=_NAME)]),
                    Code(id="ON", name=_NAME, annotations=[Annotation(id="a2", type="source", text=_NAME)]),
                    Code(id="QC", name=_NAME),
                ],
            )
        )
        registry.add(
            DataStructure(
                id="CPI",
                maintainer="SC",
                version="1.0",
                name=_NAME,
                dimensions=[
                    Dimension(id="FREQ", concept=concept_ref("FREQ")),
                    Dimension(id="GEO", concept=concept_ref("GEO")),
                ],
                attributes=[
                    Attribute(attachment=sl.AttachmentLevel.DATASET, id="OBS_STATUS", concept=concept_ref("OBS_STATUS"))
                ],
                measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
            )
        )
        yield registry


def test_a_list_collects_only_the_matching_children(reg: sl.LocalRegistry) -> None:
    """The case that had no spelling: the dimensions of each DSD."""
    frame = reg.query(q.dsd).select(
        every=_C.list(_C.id),
        dims=_C.list(_C.id, where=q.component.role == "Dimension"),
    )
    assert frame.collect().to_dicts() == [
        {"every": ["FREQ", "GEO", "OBS_STATUS", "OBS_VALUE"], "dims": ["FREQ", "GEO"]}
    ]


def test_a_count_counts_only_the_matching_children(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.dsd).select(
        n_all=_C.count(),
        n_dims=_C.count(where=q.component.role == "Dimension"),
        n_measures=_C.count(where=q.component.role == "Measure"),
    )
    assert frame.collect().to_dicts() == [{"n_all": 4, "n_dims": 2, "n_measures": 1}]


def test_no_matching_child_collects_empty_rather_than_null(reg: sl.LocalRegistry) -> None:
    """The surprising half, pinned: `[]` and `0`, never NULL.

    Consistent with the rule the upgrade guide already states for an
    unrestricted aggregate — a leaf collects to `[]`, never None — and worth a
    test because a correlated subquery returning no rows is exactly where a
    NULL would otherwise appear.
    """
    frame = reg.query(q.dsd).select(
        ids=_C.list(_C.id, where=q.component.id == "NOSUCH"),
        n=_C.count(where=q.component.id == "NOSUCH"),
    )
    assert frame.collect().to_dicts() == [{"ids": [], "n": 0}]


def test_the_column_form_takes_it_too(reg: sl.LocalRegistry) -> None:
    """`fan.col.list(where=…)`, the dotted spelling, restricted the same way."""
    codes = q.codelist.codes
    frame = reg.query(q.codelist).select(
        noted=codes.id.list(where=q.code.annotations.any(q.annotation.type == "note")),
        every=codes.id.list(),
    )
    assert frame.collect().to_dicts() == [{"noted": ["CA"], "every": ["CA", "ON", "QC"]}]


def test_a_nested_fan_out_inside_the_restriction(reg: sl.LocalRegistry) -> None:
    """`where=` reaching *through* another to-many edge — one EXISTS inside.

    The restriction compiles like a restriction anywhere else, which is the
    point of feeding it to the same scope machinery `.any()` uses rather than
    special-casing it.
    """
    codes = q.codelist.codes
    frame = reg.query(q.codelist).select(
        annotated=codes.count(where=q.code.annotations.any(q.annotation.id.is_not_null())),
    )
    assert frame.collect().to_dicts() == [{"annotated": 2}]


def test_conditions_compose_with_the_ordinary_operators(reg: sl.LocalRegistry) -> None:
    """`&` and `|`, not a variadic form.

    A restriction is evaluated per candidate child, so the same-child /
    different-child question `.any(p, q)` exists to answer does not arise —
    each child is inspected alone. AND and OR are therefore ordinary
    composition, and the OR is a real question: "dimensions, plus whatever is
    called GEO" collects a deliberately mixed set.
    """
    dimension = q.component.role == "Dimension"
    named_geo = q.component.id == "GEO"
    frame = reg.query(q.dsd).select(
        both=_C.list(_C.id, where=dimension & named_geo),
        either=_C.list(_C.id, where=(q.component.role == "Measure") | named_geo),
        neither=_C.list(_C.id, where=(q.component.role == "Measure") & named_geo),
    )
    assert frame.collect().to_dicts() == [{"both": ["GEO"], "either": ["GEO", "OBS_VALUE"], "neither": []}]


def test_an_unrestricted_aggregate_compiles_exactly_as_before(reg: sl.LocalRegistry) -> None:
    """No `AND TRUE` smuggled into every existing query.

    `_conjoin([])` is `TRUE`, so folding an empty restriction in unconditionally
    would rewrite the SQL of queries this change is not supposed to touch. The
    assertion is on the text because that *is* the claim.
    """
    sql = reg.query(q.dsd).select(ids=_C.list(_C.id)).to_sql().sql
    assert "TRUE" not in sql
    assert "WHERE s0.urn = s1.dsd_urn" in sql


def test_the_restriction_is_part_of_what_an_aggregate_means(reg: sl.LocalRegistry) -> None:
    """Two aggregates differing only by `where=` are different computations.

    `__canonical__` is what `order_by` matches on, so leaving the restriction
    out would let a filtered aggregate be ordered by an unfiltered one — the
    identity bug that method exists to prevent, one level in.
    """
    plain = _C.count()
    dims = _C.count(where=q.component.role == "Dimension")
    measures = _C.count(where=q.component.role == "Measure")

    assert plain.__canonical__() != dims.__canonical__()
    assert dims.__canonical__() != measures.__canonical__()
    assert dims.__canonical__() == _C.count(where=q.component.role == "Dimension").__canonical__()


def test_a_filtered_aggregate_can_be_ordered_by(reg: sl.LocalRegistry) -> None:
    """The identity above, used: an aggregate is projectable and orderable."""
    dims = _C.count(where=q.component.role == "Dimension")
    frame = reg.query(q.dsd).select(id=q.dsd.id, n=dims).order_by(dims)
    assert frame.collect().to_dicts() == [{"id": "CPI", "n": 2}]


def test_a_filtered_aggregate_can_be_compared(reg: sl.LocalRegistry) -> None:
    """`count(where=…) == 0` — "none of them are like this", row-preserving."""
    matched = reg.query(q.dsd).filter(_C.count(where=q.component.role == "Measure") == 1)
    assert matched.ids() == ["CPI"]

    missed = reg.query(q.dsd).filter(_C.count(where=q.component.role == "Measure") == 0)
    assert missed.ids() == []


def test_unique_takes_it_as_well(reg: sl.LocalRegistry) -> None:
    """Every aggregate, not a chosen few — the gap was uniform."""
    frame = reg.query(q.dsd).select(
        roles=_C.unique(_C.role, where=q.component.role != "Measure"),
    )
    rows = frame.collect().to_dicts()
    assert sorted(str(role) for role in rows[0]["roles"]) == ["Attribute", "Dimension"]


# ── differentially, against a computation that shares no code ─────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_a_filtered_list_agrees_with_python(cl: sl.Codelist, data: st.DataObject) -> None:
    """The restriction, checked against a direct walk over the artefact.

    Aggregates had no differential coverage before this — the two query
    oracles model *predicates* — and a filtered aggregate is exactly the shape
    where a hand-written expectation and the compiler can agree while both are
    wrong. This computes the answer from the `Code` objects instead, sharing
    nothing with the compiler but Python's own semantics.

    `annotated_forest_codelists` draws types from a two-element vocabulary so
    a generated filter can actually hit a row; drawn from `sdmx_ids` the
    property would spend every example confirming that `[]` equals `[]`.
    """
    codes = list(cl.codes)
    types = sorted({a.type for c in codes for a in c.annotations if a.type})
    if not types:
        return
    wanted = data.draw(st.sampled_from(types), label="type")

    expected = [c.id for c in codes if any(a.type == wanted for a in c.annotations)]

    with sl.LocalRegistry(":memory:") as reg:
        reg.add(cl)
        codes_fan = q.codelist.codes
        frame = reg.query(q.codelist).select(
            ids=codes_fan.id.list(where=q.code.annotations.any(q.annotation.type == wanted))
        )
        rows = frame.collect().to_dicts()
        assert sorted(str(i) for i in rows[0]["ids"]) == sorted(expected)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_a_filtered_count_agrees_with_python(cl: sl.Codelist, data: st.DataObject) -> None:
    """The same, for the aggregate whose empty case is `0` rather than `[]`."""
    codes = list(cl.codes)
    types = sorted({a.type for c in codes for a in c.annotations if a.type})
    if not types:
        return
    wanted = data.draw(st.sampled_from(types), label="type")
    expected = sum(1 for c in codes if any(a.type == wanted for a in c.annotations))

    with sl.LocalRegistry(":memory:") as reg:
        reg.add(cl)
        frame = reg.query(q.codelist).select(
            n=q.codelist.codes.count(where=q.code.annotations.any(q.annotation.type == wanted))
        )
        assert frame.collect().to_dicts()[0]["n"] == expected


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists())
def test_restricting_never_collects_more_than_not_restricting(cl: sl.Codelist) -> None:
    """A law rather than a value: the restriction is a subset, always.

    Cheap, and it holds for every codelist including the ones where no
    annotation type exists — the case the two properties above skip.
    """
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(cl)
        codes_fan = q.codelist.codes
        frame = reg.query(q.codelist).select(
            every=codes_fan.id.list(),
            noted=codes_fan.id.list(where=q.code.annotations.any(q.annotation.type == "note")),
        )
        row = frame.collect().to_dicts()[0]
        assert set(map(str, row["noted"])) <= set(map(str, row["every"]))


# ── over a group's own rows: `Query.agg`, `group_by().agg()`, `having` ────────
#
# The same `where=` on a column that does not fan out restricts which of the
# group's rows aggregate. It was dropped until it was found writing
# `llms-dsl.txt`: `cl.codes.agg(n=q.code.id.count(where=q.code.depth == 0))`
# compiled to a bare `count(*)` and answered with the whole codelist.

_CODE = q.code


@pytest.fixture
def codes(reg: sl.LocalRegistry) -> sl.Query[sl.CodeRow, Code]:
    lazy = reg.get(Codelist.urn_for(agency="SC", id="CL_GEO", version="1.0"))
    assert isinstance(lazy, sl.LazyCodelist)
    return lazy.codes


@pytest.mark.parametrize(
    ("agg", "expected"),
    [
        pytest.param(_CODE.id.count(where=_CODE.id != "QC"), 2, id="count"),
        pytest.param(_CODE.id.n_unique(where=_CODE.id != "QC"), 2, id="n_unique"),
        pytest.param(_CODE.position.sum(where=_CODE.id != "QC"), 1, id="sum"),
        pytest.param(_CODE.id.list(where=_CODE.id != "QC"), ["CA", "ON"], id="list"),
    ],
)
def test_a_root_aggregate_reads_only_the_matching_rows(
    codes: sl.Query[sl.CodeRow, Code], agg: sl.Agg[sl.CodeRow], expected: object
) -> None:
    """Every aggregate that takes `where=`, over the three codes CA, ON, QC (positions 0-2)."""
    assert codes.agg(n=agg).collect().to_dicts() == [{"n": expected}]


def test_the_restriction_reaches_through_edges(codes: sl.Query[sl.CodeRow, Code]) -> None:
    """A to-one hop the scope must join for, and a to-many edge it compiles to EXISTS."""
    frame = codes.agg(
        named=_CODE.id.count(where=_CODE.codelist.id == "CL_GEO"),
        noted=_CODE.id.count(where=_CODE.annotations.type == "note"),
    )
    assert frame.collect().to_dicts() == [{"named": 3, "noted": 1}]


def test_no_matching_row_aggregates_to_empty(codes: sl.Query[sl.CodeRow, Code]) -> None:
    """`0` and `[]`, as for a fan-out with no matching child."""
    nothing = _CODE.id == "NOSUCH"
    frame = codes.agg(n=_CODE.id.count(where=nothing), ids=_CODE.id.list(where=nothing))
    assert frame.collect().to_dicts() == [{"n": 0, "ids": []}]


def test_a_group_by_restricts_within_each_group(reg: sl.LocalRegistry) -> None:
    roles = (
        reg.query(q.component)
        .group_by(q.component.role)
        .agg(n=sl.len(), not_geo=q.component.id.count(where=q.component.id != "GEO"))
        .order_by("role")
    )
    assert [(str(role), n, not_geo) for role, n, not_geo in roles.collect().rows()] == [
        ("Attribute", 1, 1),
        ("Dimension", 2, 1),
        ("Measure", 1, 1),
    ]


def test_a_having_restricts_as_the_select_list_does(reg: sl.LocalRegistry) -> None:
    has_geo = (
        reg.query(q.component)
        .group_by(q.component.role)
        .agg(n=sl.len())
        .having(q.component.id.count(where=q.component.id == "GEO") > 0)
    )
    assert [(str(role), n) for role, n in has_geo.collect().rows()] == [("Dimension", 2)]


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_the_grouped_and_correlated_readings_agree(cl: sl.Codelist, data: st.DataObject) -> None:
    """One restriction, compiled both ways, over the same codes.

    `codelist.codes.count(where=p)` correlates a subquery over the children;
    `codes.agg(n=code.id.count(where=p))` filters the rows of one group. They
    are two compiler paths for one question, and they share no SQL — so each
    checks the other, as well as both checking Python.
    """
    codes = list(cl.codes)
    types = sorted({a.type for c in codes for a in c.annotations if a.type}) or ["none"]
    wanted = data.draw(st.sampled_from(types), label="type")
    restriction = _CODE.annotations.any(q.annotation.type == wanted)
    expected = sum(1 for c in codes if any(a.type == wanted for a in c.annotations))

    with sl.LocalRegistry(":memory:") as registry:
        registry.add(cl)
        correlated = registry.query(q.codelist).select(n=q.codelist.codes.count(where=restriction))
        lazy = registry.get(cl.urn)
        assert isinstance(lazy, sl.LazyCodelist)
        grouped = lazy.codes.agg(n=_CODE.id.count(where=restriction))
        assert correlated.collect().item() == grouped.collect().item() == expected
