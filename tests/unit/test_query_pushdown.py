"""`Capability`, `PredicateShape` and the program a plan becomes.

Step 2 of `docs/design/query-pushdown.md` §7. The step is a no-op by
construction — DuckDB declares total capability, so every program is one
`Push` and the SQL corpus does not move — which means the tests here are
almost entirely about the machinery being *right for a backend that is not
DuckDB*. Nothing else will exercise it until step 4.

Two properties carry most of the weight:

* **Every predicate kind has a shape**, counted off the module rather than
  listed by hand. A kind with no case would raise, and the alternative — a
  fallback returning the empty set — would make it push *everywhere*, since a
  capability check is a subset test and the empty set is a subset of anything.
* **The fold is part of the shape.** A backend accepting `Match` at
  `Fold.NONE` has said nothing about `Fold.CASELESS`; conflating the two lets
  a case-sensitive server answer a caseless question and return too few rows
  (§9.3).
"""

from __future__ import annotations

import attrs
import pytest

from sdmxlib.model._label_match import Fold
from sdmxlib.query import _expr as qx
from sdmxlib.query._common import canonical_key
from sdmxlib.query._plan import Plan, scanned
from sdmxlib.query._pushdown import (
    DUCKDB,
    Capability,
    PredicateShape,
    Push,
    PushdownError,
    Residual,
    is_bounded,
    plan_program,
    root_key,
    shapes_of,
)
from sdmxlib.query.accessors import DataflowRow, q

pytestmark = pytest.mark.unit


def _plan(*predicates: qx.Pred[DataflowRow]) -> Plan[DataflowRow]:
    """A dataflow plan carrying `predicates`. The root is never executed here."""
    return Plan(root=scanned(q.dataflow), predicates=predicates)


# ── shapes ────────────────────────────────────────────────────────────────


def test_a_shape_drops_the_literal_but_keeps_the_column() -> None:
    """A backend that can filter by agency can filter by *any* agency.

    Dropping the value is what makes a declaration finite; keeping the column
    is what stops "can filter by agency" being read as "can filter".
    """
    estat = shapes_of(q.dataflow.agency == "ESTAT")
    ecb = shapes_of(q.dataflow.agency == "ECB")
    assert estat == ecb
    (shape,) = estat
    assert shape.kind == "Compare"
    assert shape.column == "agency_id"  # the *physical* name, which is what a backend filters on
    assert shape.op == "="
    assert shape.path == ()


def test_a_shape_keeps_the_path_it_was_reached_through() -> None:
    """`dsd.id` and `id` are different questions for a backend that cannot join.

    This is the whole reason capability is keyed by prefix (§9.1): a shape
    that forgot its path would let a REST backend claim it can filter
    dataflows by their DSD's id because it can filter *something* by id.
    """
    (local,) = shapes_of(q.dataflow.id == "X")
    (crossed,) = shapes_of(q.dataflow.dsd.id == "X")
    assert local.path == ()
    assert local != crossed
    # Two hops for one attribute: `dsd` is a *composite* edge crossing the
    # dataflow extension table and back out to the spine. So a declaration is
    # keyed on the declared route, not on the spelling a caller writes — a
    # REST backend keying on `("dsd",)` would match nothing, silently.
    assert crossed.path == ("dataflow", "dsd")
    assert crossed.column == "resource_id"


def test_the_fold_is_part_of_the_shape() -> None:
    """#255 and #222 pinned matching locally. A push escapes that pinning."""
    (sensitive,) = shapes_of(q.dataflow.id.str.contains("cpi"))
    (caseless,) = shapes_of(q.dataflow.id.str.caseless.contains("cpi"))
    assert sensitive.fold is Fold.NONE
    assert caseless.fold is not Fold.NONE
    assert sensitive != caseless


def test_a_composite_carries_its_own_shape_and_its_children() -> None:
    """A backend that cannot evaluate OR must not be handed one.

    It would otherwise pass on the strength of its leaves, both of which it
    genuinely can evaluate.
    """
    shapes = shapes_of((q.dataflow.agency == "A") | (q.dataflow.agency == "B"))
    assert PredicateShape(kind="BoolOp", op="or") in shapes
    assert PredicateShape(kind="Compare", column="agency_id", op="=") in shapes


def test_every_predicate_kind_has_a_shape() -> None:
    """Counted off the module, so a new `Pred` fails here rather than silently.

    The failure mode this prevents is specific: `shapes_of` raising is safe,
    but a `shapes_of` that *returned* something empty would make the new
    predicate acceptable to every backend, because `accepts` is a subset test.
    """
    kinds = {
        cls.__name__
        for cls in vars(qx).values()
        if isinstance(cls, type) and issubclass(cls, qx.Pred) and cls is not qx.Pred
    }
    covered = {
        "Compare",
        "Match",
        "IsNull",
        "ArrayHas",
        "InSet",
        "InRelation",
        "AggCompare",
        "RowIn",
        "BoolOp",
        "Not",
        "Quantified",
    }
    assert kinds == covered, f"predicate kinds without a shape: {kinds - covered}"


def test_an_unshaped_predicate_raises_rather_than_pushing() -> None:
    """The guard above catches this at review time; this catches it at runtime."""

    class Novel(qx.Pred[DataflowRow]):
        pass

    with pytest.raises(NotImplementedError, match="no declared shape"):
        shapes_of(Novel())


# ── capability ────────────────────────────────────────────────────────────


def test_total_and_a_declaration_cannot_be_stated_together() -> None:
    """One would be dead text, and dead text drifts."""
    with pytest.raises(ValueError, match="would never be read"):
        Capability(
            name="confused",
            total=True,
            pushes={(): frozenset({PredicateShape(kind="Compare", column="agency", op="=")})},
        )


def test_declaring_nothing_is_the_opposite_of_declaring_no_limits() -> None:
    """Why `total` is a field rather than an empty mapping meaning "anything".

    Encoding them the same way is how a REST backend whose declaration was
    never filled in would quietly acquire DuckDB's powers.
    """
    silent = Capability(name="silent", scans=frozenset({DATAFLOW}))
    assert not silent.accepts((), shapes_of(q.dataflow.agency == "ESTAT"))
    assert DUCKDB.accepts((), shapes_of(q.dataflow.agency == "ESTAT"))


def test_a_capability_accepts_only_what_it_declares_at_that_prefix() -> None:
    agency = PredicateShape(kind="Compare", column="agency", op="=")
    narrow = Capability(name="narrow", pushes={(): frozenset({agency})})
    assert narrow.accepts((), {agency})
    assert not narrow.accepts(("dsd",), {agency})


# ── the program ───────────────────────────────────────────────────────────


def test_duckdb_yields_one_push_carrying_the_plan_unchanged() -> None:
    """The no-op that keeps the corpus byte-identical.

    Compared with `canonical_key` rather than `==`: a `Plan` holds an
    accessor, and an accessor refuses comparison outright — see `Plan`.
    """
    plan = _plan(q.dataflow.agency == "ESTAT", q.dataflow.id.str.contains("cpi"))
    program = plan_program(plan, DUCKDB)
    assert program.backend == "duckdb"
    assert len(program.steps) == 1
    (step,) = program.steps
    assert isinstance(step, Push)
    assert canonical_key(step.plan) == canonical_key(plan)


def test_a_partial_backend_splits_into_a_push_and_a_residual() -> None:
    """Push what it declared, keep the rest — and keep it in *order*.

    Not a `(pushed, residual)` pair, because the shape has to hold the §9.1
    rewrite: two calls with a local step between them. For DuckDB every
    program has length one, so a pair would look sufficient right through
    step 3 and fail at step 4.
    """
    pushable = q.dataflow.agency == "ESTAT"
    local = q.dataflow.id.str.contains("cpi")
    narrow = Capability(
        name="narrow",
        pushes={(): shapes_of(pushable)},
        scans=frozenset({root_key(q.dataflow)}),
        # Declared, or the residual below is refused outright — see the
        # boundedness tests. Pushing the agency is what makes the scan a
        # request a caller could have issued in its own right.
        bounding={root_key(q.dataflow): shapes_of(pushable)},
    )

    program = plan_program(_plan(pushable, local), narrow)

    assert program.backend == "narrow"
    first, second = program.steps
    assert isinstance(first, Push)
    assert canonical_key(first.plan) == canonical_key(_plan(pushable))
    assert second == Residual((local,))
    assert len(program.pushes) == 1
    assert program.residuals[0].predicates == (local,)


def test_the_program_counts_round_trips_not_rows() -> None:
    """`pushes` is the cost unit §9.2 says the bound must be stated in.

    A program of fixed length is affordable; one whose length grows with the
    data is #276 again whatever any row-count bound says.
    """
    program = plan_program(_plan(q.dataflow.agency == "ESTAT"), DUCKDB)
    assert len(program.pushes) == 1
    assert program.residuals == ()


# ── the refusal rule (§5) ─────────────────────────────────────────────────
#
# The safety-critical half of the design, and the one with no symptom when it
# is wrong: the rows come back *correct*, having fetched every artefact in the
# registry to filter three of them locally. A test is the only thing that can
# tell the difference.

_AGENCY = q.dataflow.agency == "ESTAT"
_NAME = q.dataflow.id.str.contains("cpi")

#: A registry that filters by agency and nothing else, and is bounded by
#: agency — the SDMX REST shape, which is where §5's rule comes from.
DATAFLOW = root_key(q.dataflow)

NARROW = Capability(
    name="narrow",
    pushes={(): shapes_of(_AGENCY)},
    scans=frozenset({DATAFLOW}),
    bounding={DATAFLOW: shapes_of(_AGENCY)},
)


def test_a_residual_over_a_bounded_scan_is_allowed() -> None:
    """The naive rule — refuse whenever there is a residual — is too strict.

    It would reject an agency filter plus a local name filter, which is a
    good query over a few hundred rows and the ordinary case.
    """
    program = plan_program(_plan(_AGENCY, _NAME), NARROW)
    assert len(program.pushes) == 1
    assert program.residuals[0].predicates == (_NAME,)


def test_a_residual_over_an_unbounded_scan_is_refused() -> None:
    """The permissive rule is the catastrophe: every dataflow, fetched to
    filter locally, arrived at silently."""
    with pytest.raises(PushdownError) as caught:
        plan_program(_plan(_NAME), NARROW)
    message = str(caught.value)
    assert "narrow" in message
    assert "unbounded" in message
    assert "agency_id" in message, "the refusal must name the fix, not just the problem"


def test_a_backend_that_declares_no_bound_bounds_nothing() -> None:
    """Empty `bounding` means nothing bounds it, not that everything is bounded.

    `all(shape in pushed for shape in ())` is vacuously true, which would hand
    a backend nobody has finished declaring the right to unlimited residuals —
    the opposite of the safe reading, and the more dangerous by exactly the
    margin this rule protects.
    """
    undeclared = Capability(
        name="undeclared",
        pushes={(): shapes_of(_AGENCY)},
        scans=frozenset({DATAFLOW}),
    )
    with pytest.raises(PushdownError):
        plan_program(_plan(_AGENCY, _NAME), undeclared)


def test_duckdb_is_bounded_without_declaring_a_bound() -> None:
    """Its unbounded scan is a table scan, not a fetch of every artefact ever
    published, so §5's rule is not about it."""
    assert is_bounded(Push(_plan(_NAME)), DUCKDB)
    assert not is_bounded(Push(_plan(_NAME)), NARROW)


def test_the_bound_is_checked_per_scan_not_per_program() -> None:
    """§5's per-scan rule, asserted on the unit it applies to.

    Trivially satisfied today, since one scan is produced. Written now because
    retrofitting it once the §9.1 rewrite lands means auditing a rewrite
    rather than a scan — and the failure it prevents is a rewrite that bounds
    the outer scan by a set computed from an unbounded inner one, which passes
    any whole-program check while having moved the problem one step down.
    """
    program = plan_program(_plan(_AGENCY, _NAME), NARROW)
    assert all(is_bounded(push, NARROW) for push in program.pushes)


# ── the slice does not survive a residual ─────────────────────────────────


def test_a_slice_comes_local_when_anything_else_does() -> None:
    """Fetching 10 rows and *then* filtering yields fewer than 10, silently.

    So the push drops the window and the local step carries it — which also
    means the backend returns more rows than the caller asked for, and that is
    the correct trade rather than an oversight.
    """
    plan = attrs.evolve(_plan(_AGENCY, _NAME), limit_n=10, offset_n=5)
    program = plan_program(plan, NARROW)
    (push,) = program.pushes
    assert push.plan.limit_n is None
    assert push.plan.offset_n == 0
    assert program.residuals[0].limit_n == 10
    assert program.residuals[0].offset_n == 5


def test_a_slice_is_pushed_when_nothing_is_left_over() -> None:
    plan = attrs.evolve(_plan(_AGENCY), limit_n=10)
    (push,) = plan_program(plan, NARROW).pushes
    assert push.plan.limit_n == 10
    assert plan_program(plan, NARROW).residuals == ()


def test_a_backend_that_cannot_slice_keeps_the_window_local() -> None:
    """Two different questions: whether the wire format has a LIMIT, and
    whether pushing one would still mean the same thing."""
    sliceless = attrs.evolve(NARROW, slices=False)
    plan = attrs.evolve(_plan(_AGENCY), limit_n=10)
    program = plan_program(plan, sliceless)
    assert program.pushes[0].plan.limit_n is None
    assert program.residuals[0].limit_n == 10


def test_a_backend_that_cannot_sort_keeps_the_order_local() -> None:
    orderless = attrs.evolve(NARROW, orders=False)
    plan = attrs.evolve(_plan(_AGENCY), order_keys=(qx.OrderKey(q.dataflow.id, ascending=True),))
    program = plan_program(plan, orderless)
    assert program.pushes[0].plan.order_keys == ()
    assert program.residuals[0].order == plan.order


def test_an_empty_local_step_is_not_carried() -> None:
    """A `Residual` that would do nothing is noise in `explain()` and a step a
    reader has to check before dismissing."""
    program = plan_program(_plan(_AGENCY), NARROW)
    assert program.residuals == ()
    assert len(program.steps) == 1
