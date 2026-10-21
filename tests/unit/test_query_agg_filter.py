"""An aggregate in filter position — `q.code.children.count() == 0`.

"This fan-out is empty" needed an **affirmative** spelling. It was reachable as
`~fanout.any()`, and a language where the only way to say a common thing is to
negate another thing is one that has not named it.

Checked against the peers before choosing, and they converge on counting:

| | spelling |
| --- | --- |
| polars | `col.list.len() == 0` |
| ibis | `arr.length() == 0` |
| Django | `annotate(n=Count("children")).filter(n=0)` |
| SQLAlchemy | `Node.children == None` — affirmative, but reads as a null test |

Django's *other* spelling, `filter(children__isnull=True)`, compiles to a
`LEFT JOIN … IS NULL`. Deliberately not copied: the join multiplies rows in the
positive direction, and a correlated subquery does not.

Counting also generalises where a bespoke `none()` would not — `> 1`, `>= 3`
and `!= 0` all fall out, and `none()` was measured to buy a name and no
capability, since `~any()` already produced identical rows.

The compiled shape is the claim: a correlated **scalar** subquery in the
`WHERE`, not a `GROUP BY … HAVING`, which would collapse the rows being
filtered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.query._expr import Pred
from sdmxlib.query.accessors import CodeRow, q
from tests.support.factories import concept_ref
from tests.support.narrow import present

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=ESTAT:CL_GEO(1.0)"


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """A three-deep tree with a branch.

    `W` has two children, `CA` has one, `MX` and `ON` have none — so every
    threshold below separates a different set. A flat codelist would pass most
    of this file with the counting broken.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_GEO",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Geography"},
                codes=[
                    Code(id="W", name={"en": "World"}),
                    Code(id="CA", name={"en": "Canada"}, parent_id="W"),
                    Code(id="ON", name={"en": "Ontario"}, parent_id="CA"),
                    Code(id="MX", name={"en": "Mexico"}, parent_id="W"),
                ],
            )
        )
        yield registry


def _ids(reg: sl.LocalRegistry, predicate: Pred[CodeRow]) -> list[str]:
    return sorted(reg.query(q.code).filter(q.code.codelist.urn == _URN).filter(predicate).ids())


# ── the empty case, said affirmatively ────────────────────────────────────


def test_count_zero_is_the_empty_fan_out(reg: sl.LocalRegistry) -> None:
    assert _ids(reg, q.code.children.count() == 0) == ["MX", "ON"]


def test_it_agrees_with_the_verb_it_replaces(reg: sl.LocalRegistry) -> None:
    """The differential that matters: same question, two compilers."""
    verb = sorted(c.id for c in present(reg.codelist(_URN)).leaves().collect())
    assert verb == _ids(reg, q.code.children.count() == 0)


def test_it_agrees_with_the_negation_it_replaces(reg: sl.LocalRegistry) -> None:
    """`~any()` still works and still means the same. The count form is a
    better *name* for the question, not a different answer."""
    assert _ids(reg, q.code.children.count() == 0) == _ids(reg, ~q.code.children.any())


# ── and it generalises, which a bespoke `none()` would not ────────────────


@pytest.mark.parametrize(
    ("name", "predicate", "expected"),
    [
        ("== 0", q.code.children.count() == 0, ["MX", "ON"]),
        ("!= 0", q.code.children.count() != 0, ["CA", "W"]),
        (">= 1", q.code.children.count() >= 1, ["CA", "W"]),
        ("> 1", q.code.children.count() > 1, ["W"]),
        ("< 2", q.code.children.count() < 2, ["CA", "MX", "ON"]),
        ("<= 1", q.code.children.count() <= 1, ["CA", "MX", "ON"]),
    ],
)
def test_every_comparison_operator(
    reg: sl.LocalRegistry, name: str, predicate: Pred[CodeRow], expected: list[str]
) -> None:
    """Each threshold picks a different set, so an operator compiled as its
    opposite fails rather than coinciding."""
    assert _ids(reg, predicate) == expected, name


def test_the_counts_themselves_are_right(reg: sl.LocalRegistry) -> None:
    """The filter and the projection must agree — they share one subquery, and
    this is what says so rather than assuming it."""
    frame = (
        reg.query(q.code)
        .filter(q.code.codelist.urn == _URN)
        .order_by(q.code.id)
        .select(id=q.code.id, n=q.code.children.count())
        .collect()
    )
    assert frame.to_dicts() == [
        {"id": "CA", "n": 1},
        {"id": "MX", "n": 0},
        {"id": "ON", "n": 0},
        {"id": "W", "n": 2},
    ]


# ── the compiled shape is the claim ───────────────────────────────────────


def test_it_is_a_correlated_scalar_subquery(reg: sl.LocalRegistry) -> None:
    """Not a `GROUP BY … HAVING`, which would collapse the rows being
    filtered, and not a join, which would multiply them."""
    sql = reg.query(q.code).filter(q.code.children.count() == 0).to_sql().sql
    assert "(SELECT count(*)" in sql
    assert "GROUP BY" not in sql
    assert "HAVING" not in sql
    assert "JOIN" not in sql


def test_it_does_not_multiply_rows(reg: sl.LocalRegistry) -> None:
    """The reason Django's `children__isnull=True` spelling was not copied: a
    LEFT JOIN gives one row per child in the positive direction."""
    found = reg.query(q.code).filter(q.code.codelist.urn == _URN, q.code.children.count() >= 1)
    assert found.count() == 2
    assert sorted(found.ids()) == ["CA", "W"]


# ── it is an aggregate, not a tree feature ────────────────────────────────


def test_it_works_on_any_fan_out(reg: sl.LocalRegistry) -> None:
    """ "A DSD with no dimensions" is the same shape as "a code with no
    children", which is the point of putting this on the aggregate rather than
    on the tree."""
    reg.add(
        ConceptScheme(
            id="CS",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Concepts"},
            concepts=[Concept(id="REF_AREA", name={"en": "Area"})],
        )
    )
    area = Ref[object].unresolved(Concept.urn_for(agency="ESTAT", scheme="CS", id="REF_AREA", version="1.0"))
    reg.add(
        DataStructure(
            id="GEO",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Geo"},
            dimensions=[Dimension(id="REF_AREA", concept=area), Dimension(id="FREQ", concept=concept_ref("FREQ"))],
        )
    )
    reg.add(DataStructure(id="EMPTY", maintainer=Agency(id="ESTAT"), name={"en": "Empty"}))
    assert reg.query(q.dsd).filter(q.dsd.components.count() == 0).ids() == ["EMPTY"]
    assert reg.query(q.dsd).filter(q.dsd.components.count() > 1).ids() == ["GEO"]


def test_it_composes_like_any_other_predicate(reg: sl.LocalRegistry) -> None:
    assert _ids(reg, (q.code.children.count() == 0) & (q.code.depth >= 2)) == ["ON"]
    assert _ids(reg, ~(q.code.children.count() == 0)) == ["CA", "W"]
    assert _ids(reg, (q.code.children.count() > 1) | (q.code.depth >= 2)) == ["ON", "W"]


# ── the hole this closed ──────────────────────────────────────────────────


def test_a_comparison_no_longer_collapses_to_a_bool(reg: sl.LocalRegistry) -> None:
    """`Agg` had no `__eq__`, so `count() == 0` was Python's identity check and
    evaluated to `False`. `filter` happened to reject a bool, so it raised
    rather than silently matching nothing — but only by luck, and `> 1` failed
    with a bare `'>' not supported` from the interpreter.

    Same class as the `|` precedence hole: an expression that reads like a
    predicate and is not one.
    """
    from sdmxlib.query._expr import AggCompare

    assert isinstance(q.code.children.count() == 0, AggCompare)
    assert isinstance(q.code.children.count() > 1, AggCompare)


def test_there_is_no_third_quantifier(reg: sl.LocalRegistry) -> None:
    """`none()` was added and removed in one milestone. It bought a name and no
    capability — `~any()` produced identical rows — and counting says the same
    thing affirmatively while generalising past zero."""
    assert not hasattr(q.code.children, "none")
    assert hasattr(q.code.children, "any")
    assert hasattr(q.code.children, "every")
