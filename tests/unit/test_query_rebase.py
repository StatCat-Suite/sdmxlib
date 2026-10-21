"""Re-rooting a predicate, checked by making DuckDB run both formulations.

`_rebase` exists for the §9.1 rewrite: `q.dataflow` filtered by
`dsd.<something>` becomes `q.dsd` filtered by `<something>`, whose rows a
second step matches by key. Step 3's write-up said this could not be checked
before a second backend existed. That was too pessimistic, and the correction
is the point of this file.

**The rewritten form is already expressible.** `is_in` over a single-column
sub-query is exactly "match the rows of that other scan by key", so the target
of the rewrite can be written by hand *today* and executed by DuckDB. That
makes the property a real differential — two genuinely different executions
compared against each other — rather than an assertion about the shape of a
tree:

    A = query(dataflow).filter(P)                                  # joined
    B = query(dataflow).filter(fk.is_in(query(dsd).filter(rebase(P))))   # rewritten

Same rows, or the rebase is wrong. This is the third-implementation discipline
the oracle notes describe, and it catches the failure mode that matters: a
rebased column that still *compiles* but asks a different question.

What it cannot catch is a column class whose extra state is dropped, if no
fixture distinguishes it — so `test_a_rebased_label_keeps_its_language` and
the slot census exist beside it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency
from sdmxlib.query import _expr as qx
from sdmxlib.query._rebase import RebaseError, rebase, rebase_column
from sdmxlib.query.accessors import DataflowRow, q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

#: The route `q.dataflow.dsd` resolves to — two hops, because `dsd` is a
#: composite edge crossing the dataflow extension table and back out to the
#: spine. Written out rather than derived, so a change to the edge graph fails
#: here loudly instead of quietly rebasing onto the wrong root.
DSD_PREFIX = ("dataflow", "dsd")


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two DSDs, three dataflows, and one concept shared by only one DSD.

    Shaped so every query below has a non-empty *and* a non-total answer: a
    rewrite that dropped its filter entirely, and one that matched nothing,
    both look wrong here rather than plausible.
    """
    with sl.LocalRegistry(":memory:") as registry:
        estat = Agency(id="ESTAT")
        registry.add(
            ConceptScheme(
                id="CS",
                maintainer=estat,
                name={"en": "CS"},
                concepts=[
                    Concept(id="REF_AREA", name={"en": "Reference area"}),
                    Concept(id="FREQ", name={"en": "Frequency"}),
                ],
            )
        )
        registry.add(
            Codelist(
                id="CL_FREQ",
                maintainer=estat,
                name={"en": "Freq"},
                codes=[Code(id="A", name={"en": "Annual"})],
            )
        )
        with_area = DataStructure(
            id="WITH_AREA",
            maintainer=estat,
            name={"en": "has a reference area"},
            # Only this one, so `description.is_null()` discriminates — a
            # column null everywhere makes the predicate over it vacuous, and
            # the property would spend the example agreeing that everything
            # matches everything.
            description={"en": "geography and frequency"},
            dimensions=[
                Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
                Dimension(id="FREQ", concept=concept_ref("FREQ")),
            ],
        )
        no_area = DataStructure(
            id="NO_AREA",
            maintainer=estat,
            name={"en": "frequency only"},
            dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"))],
        )
        registry.add(with_area)
        registry.add(no_area)
        for flow, dsd in [("FLOW_A", with_area), ("FLOW_B", with_area), ("FLOW_C", no_area)]:
            registry.add(Dataflow(id=flow, maintainer=estat, name={"en": flow}, structure=dsd))
        yield registry


def _joined(registry: sl.LocalRegistry, predicate: qx.Pred[DataflowRow]) -> list[str]:
    """The query as written — one statement, one join."""
    rows = registry.query(q.dataflow).filter(predicate).select(q.dataflow.id).collect()
    return sorted(str(v) for v in rows.to_series())


def _rewritten(registry: sl.LocalRegistry, predicate: qx.Pred[DataflowRow]) -> list[str]:
    """The same question as the §9.1 rewrite states it: two scans, matched by key.

    `q.dataflow.dsd.urn` trims to the dataflow's own stored reference rather
    than joining, so this really is the two-step shape and not the one-step
    one wearing a subquery.
    """
    inner = registry.query(q.dsd).filter(rebase(predicate, DSD_PREFIX)).select(q.dsd.urn)
    outer = registry.query(q.dataflow).filter(q.dataflow.dsd.urn.is_in(inner))
    rows = outer.select(q.dataflow.id).collect()
    return sorted(str(v) for v in rows.to_series())


# ── the differential ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["equality", "match", "in_set", "boolean", "negation", "quantified", "aggregate", "is_null", "localised_label"],
)
def test_the_rewritten_form_returns_what_the_joined_form_does(reg: sl.LocalRegistry, name: str) -> None:
    """The property the whole rewrite rests on, for one predicate kind each.

    A rebase that compiles but re-roots wrongly shows up here as different
    rows — which is the only signal there is, since both formulations are
    valid SQL either way.
    """
    predicates: dict[str, qx.Pred[DataflowRow]] = {
        "equality": q.dataflow.dsd.id == "WITH_AREA",
        "match": q.dataflow.dsd.id.str.contains("WITH"),
        "in_set": q.dataflow.dsd.id.is_in(["WITH_AREA", "MISSING"]),
        "boolean": (q.dataflow.dsd.id == "WITH_AREA") | (q.dataflow.dsd.id == "ABSENT"),
        "negation": ~(q.dataflow.dsd.id == "NO_AREA"),
        "quantified": q.dataflow.dsd.dimensions.any(q.component.id == "REF_AREA"),
        "aggregate": q.dataflow.dsd.dimensions.count() == 2,
        "is_null": q.dataflow.dsd.description["en"].is_null(),
        "localised_label": q.dataflow.dsd.name["en"].str.contains("reference"),
    }
    predicate = predicates[name]
    joined = _joined(reg, predicate)
    assert _rewritten(reg, predicate) == joined
    # A rewrite matching everything, or nothing, agrees with a broken join.
    # Requiring a *partial* answer is what makes the agreement mean something.
    assert 0 < len(joined) < 3, f"{name} does not discriminate: {joined}"


# ── what the differential cannot see ──────────────────────────────────────


def test_a_rebased_label_keeps_its_language() -> None:
    """The failure `copy.copy` exists to prevent, and the differential misses.

    A per-class rebase calling `Label.__init__(path, table, column)` drops the
    language, which #306 already found keying `name["en"]` and `name["fr"]`
    identically in `canonical_key`. Both spellings still compile, and a
    fixture with one language cannot tell them apart.
    """
    label = q.dsd.name["fr"]
    rebased = rebase_column(label, ())
    assert rebased.column == label.column
    assert rebased.__canonical__() == label.__canonical__()


def test_a_rebased_column_is_a_copy_not_a_mutation() -> None:
    """The original is shared — it is the accessor's own column object."""
    original = q.dataflow.dsd.id
    rebased = rebase_column(original, DSD_PREFIX)
    assert original.path == DSD_PREFIX
    assert rebased.path == ()


def test_every_column_slot_holding_columns_is_rebased() -> None:
    """Counted off the classes, not trusted.

    A nesting slot missed here leaves an inner column pointing at the old
    root: it compiles, and it answers a different question. `_source` and
    `_partition` are `Win`'s, `_operands` is the computed expressions'.
    """
    from sdmxlib.query._rebase import _NESTED_SLOTS

    holders = {
        slot
        for cls in vars(qx).values()
        if isinstance(cls, type) and issubclass(cls, qx.AnyCol)
        for slot in getattr(cls, "__slots__", ())
        if slot in {"_source", "_partition", "_operands"}
    }
    assert holders <= set(_NESTED_SLOTS), f"column slots holding columns are not all rebased: {holders}"


# ── the refusals ──────────────────────────────────────────────────────────


def test_a_column_outside_the_prefix_is_refused() -> None:
    """Not an optimisation boundary — a different question.

    `q.dataflow.id` re-rooted at `dsd` would silently become the DSD's id.
    """
    with pytest.raises(RebaseError, match="does not start with"):
        rebase(q.dataflow.id == "FLOW_A", DSD_PREFIX)


def test_a_set_operation_is_refused(reg: sl.LocalRegistry) -> None:
    """Its subquery is already compiled against the outer root's key columns."""
    other = reg.query(q.dataflow).filter(q.dataflow.id == "FLOW_A")
    combined = reg.query(q.dataflow).intersect(other)
    (predicate,) = combined.predicates
    with pytest.raises(RebaseError, match="already compiled"):
        rebase(predicate, DSD_PREFIX)


def test_an_unknown_predicate_is_refused_rather_than_passed_through() -> None:
    """Returning it unchanged would leave the planner believing it had pushed
    something it had not — a wrong answer with no error."""

    class Novel(qx.Pred[DataflowRow]):
        pass

    with pytest.raises(RebaseError, match="no rebase rule"):
        rebase(Novel(), DSD_PREFIX)
