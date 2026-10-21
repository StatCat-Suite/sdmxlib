"""The composition laws every query DSL obeys, asserted across three of them (#293).

Each DSL had a law per clause and no stated principle, so every new clause was
a fresh argument and the answers drifted. #264's design review found the laws
were already consistent — they just were not written down, and nothing enforced
them. `narrow_limit` has a unit test; the invariant it is an instance of had
nothing, so a new clause could violate it silently. That is not hypothetical:
#264's first implementation *did* violate it, and only a hand-written test
caught it.

## The principle

> **Every row-set composition refines the query. Nothing a later caller adds
> can undo what an earlier call established.**

Stated in `sdmxlib.query._common`. Three laws follow, and this file
generates them.

**Monotonicity.** `rows(c(q))` is a subset of `rows(q)`. This is the one that
matters: it is what makes a helper's constraint survive a caller, and it is
what #264's `(start, length)` window broke — `base.offset(8)` returned rows
`base` had excluded.

**Idempotence.** `c(c(q))` is `c(q)` for the same argument. Applying a
constraint twice states it twice; it does not apply it twice.

**Commutativity.** Order does not change the result — with one carve-out.

## The carve-out, which is load-bearing rather than an excuse

`limit` and `offset` do **not** commute, deliberately (#264). Narrowing,
idempotence and commutativity are not simultaneously available for that pair:
SQL's `LIMIT n OFFSET m` buys commutativity by making `n` a length measured
from `m`, which is exactly what lets a later `offset` slide the window past
rows an earlier `limit` excluded. We chose narrowing. `offset(5).limit(10)` is
ten rows from five; `limit(10).offset(5)` is five rows — of the first ten, skip
five.

So the pair is excluded from commutativity and asserted *non*-commutative
instead, on a fixture where the two answers differ. A carve-out nobody checks
becomes a place to hide a bug.

## What is not a row-set composition

`order_by` changes the order, not the membership — applying the refinement
principle to it by symmetry was the category error behind #294. It is
asserted here to be row-set *preserving*, which is a stronger and more useful
claim than monotonicity. (`with_annotations` was the other such clause until
#306 removed it: `collect()` now hydrates whole artefacts.)

`MetadataValueQuery` is gone (#306) — `reg.reported_attributes()` is now the
generic `Query`, whose laws are asserted in `test_query_object.py`. It was
absent from these properties before that anyway: it had no generative strategy,
and inventing
one here to reach a fourth DSL would test the strategy rather than the laws.
Its clauses are the same shared code paths, covered by the three below.
"""

from __future__ import annotations

import annotationlib

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.hierarchy import Hierarchy
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_hierarchy
from tests.support.narrow import present
from tests.support.strategies import annotated_forest_codelists, forest_hierarchies

pytestmark = pytest.mark.unit

#: Each example persists into a fresh in-memory DuckDB. Same scaling as the
#: other query properties so `HYPOTHESIS_PROFILE=deep` still explores hard.
_EXAMPLES = max(5, present(settings.default).max_examples // 5)


def _bounded(data: st.DataObject, n: int) -> int:
    """A limit or offset that can actually bite, given *n* rows.

    Drawn against the row count rather than from a fixed range: on a two-code
    codelist every offset above two is empty, and the property would spend its
    examples confirming that the empty set is a subset of everything.
    """
    return data.draw(st.integers(min_value=0, max_value=max(1, n + 1)), label="bound")


# ── The laws, over the codelist DSL ──────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_row_set_compositions_only_narrow(cl: Codelist, data: st.DataObject):
    """Monotonicity, over every row-set clause the codelist DSL has.

    The law #264's first implementation broke. Asserted as containment rather
    than on specific ids, because containment *is* the claim: a later caller
    cannot recover a row an earlier one excluded.

    Two starting points, because the interesting failures are second calls, not
    first ones — a fresh query narrows trivially. From an *unsliced* start every
    clause is legal; from a sliced one only `limit` and `offset` are, since
    #296's guard refuses the rest.
    """
    ids = [c.id for c in cl.codes]
    target = data.draw(st.sampled_from(ids), label="target")

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        base = present(reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))).codes

        unsliced = base.filter(q.code.id != target) if len(ids) > 1 else base
        before = set(unsliced.ids())
        # `qualify`, `intersect`, `difference` and `after` joined the surface
        # with the path language (#306) and narrow like the rest. Included
        # here rather than left to `test_every_composition_method_is_classified`
        # alone, so the classification is a claim this property backs.
        other = base.filter(q.code.depth == 0)
        top = q.code.id.rank().over(q.code.depth) == 1
        for narrowed in (
            unsliced.filter(q.code.parent.id.is_null()),
            unsliced.filter(q.code.ancestors.id == target),
            unsliced.qualify(top),
            unsliced.intersect(other),
            unsliced.difference(other),
            unsliced.order_by(q.code.id).after(unsliced.order_by(q.code.id).page(1).cursor),
            unsliced.limit(_bounded(data, len(ids))),
            unsliced.offset(_bounded(data, len(ids))),
        ):
            assert set(narrowed.ids()) <= before

        sliced = base.offset(_bounded(data, len(ids))).limit(_bounded(data, len(ids)))
        before = set(sliced.ids())
        for narrowed in (sliced.limit(_bounded(data, len(ids))), sliced.offset(_bounded(data, len(ids)))):
            assert set(narrowed.ids()) <= before


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_row_set_compositions_are_idempotent(cl: Codelist, data: st.DataObject):
    """Applying a constraint twice states it twice, it does not apply it twice."""
    ids = [c.id for c in cl.codes]
    target = data.draw(st.sampled_from(ids), label="target")
    n = _bounded(data, len(ids))

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        base = present(reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))).codes
        pred = q.code.id != target
        assert base.filter(pred).filter(pred).ids() == base.filter(pred).ids()
        assert base.limit(n).limit(n).ids() == base.limit(n).ids()
        assert base.offset(n).offset(n).ids() == base.offset(n).ids()


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_every_ordering_either_commutes_or_is_refused(cl: Codelist, data: st.DataObject):
    """The law in its strongest form: **no ordering diverges silently.**

    Either both orders of a pair give the same rows, or one of them raises.
    That is a stronger claim than "these pairs commute", and it is the one the
    DSL can actually make once #296's guard is in place — it leaves no third
    category where order changes the answer without saying so.

    The first version of this property asserted plain commutativity for
    `filter` against `limit`, and it *failed*: with the filter compiled into
    the ``WHERE`` it runs before the ``LIMIT``, so ``q.limit(1)`` was ``['0']``
    while ``q.limit(1).filter(id != '0')`` was ``['1']`` — a row the query
    being refined did not contain. The guard is what makes the principle true
    rather than aspirational, which is why #296 landed with #293 instead of
    after it.

    `limit`/`offset` is the one pair that is legal both ways and does not
    commute; it has its own property below.
    """
    ids = [c.id for c in cl.codes]
    target = data.draw(st.sampled_from(ids), label="target")
    n = _bounded(data, len(ids))

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        rows = present(reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))).codes

        clauses = {
            "filter": lambda x: x.filter(q.code.id != target),
            "filter2": lambda x: x.filter(q.code.parent.id.is_null()),
            "descendants": lambda x: x.filter(q.code.ancestors.id == target),
            "order_by": lambda x: x.order_by(q.code.id),
            "limit": lambda x: x.limit(n),
            "offset": lambda x: x.offset(n),
        }
        names = sorted(clauses)
        for first in names:
            for second in names:
                if {first, second} == {"limit", "offset"}:
                    continue  # legal both ways, deliberately non-commuting
                forward = _apply(rows, clauses[first], clauses[second])
                backward = _apply(rows, clauses[second], clauses[first])
                assert forward == backward or None in (forward, backward), (
                    f"{first} then {second} = {forward}, reversed = {backward}: "
                    "an ordering that neither commutes nor raises"
                )


def _apply(query, *clauses):
    """Rows after applying *clauses* in order, or ``None`` if the DSL refused.

    The refusal is the point: a `ValueError` here is a *pass*, because the
    principle allows a composition to be rejected and forbids it from quietly
    meaning something else.
    """
    for clause in clauses:
        try:
            query = clause(query)
        except ValueError:
            return None
    return query.ids()


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_limit_and_offset_are_the_one_non_commuting_pair(cl: Codelist, data: st.DataObject):
    """The carve-out, asserted as a *non*-law rather than merely excluded.

    A carve-out nobody checks is a place to hide a bug. Both orders are pinned
    against the plain-Python slice they mean, so a change to either one fails
    here rather than quietly re-entering the property above.

    `assume` is not used: the two orders coincide whenever the offset is zero
    or the window does not reach the edge, and those are legitimate examples.
    Pinning both against slices holds in every case.
    """
    ids = [c.id for c in cl.codes]
    off = _bounded(data, len(ids))
    lim = _bounded(data, len(ids))

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        q = present(reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))).codes
        everything = q.ids()

        # offset then limit: `lim` rows starting at `off`.
        assert q.offset(off).limit(lim).ids() == everything[off : off + lim]
        # limit then offset: of the first `lim`, skip `off`.
        assert q.limit(lim).offset(off).ids() == everything[:lim][off:]


# ── Clauses that are not row-set compositions ────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists(), data=st.data())
def test_sort_by_preserves_the_row_set(cl: Codelist, data: st.DataObject):
    """Not governed by refinement, and stronger than it.

    `order_by` permutes. Asserting *equality* of the row set rather than
    containment is the point — an `order_by` that dropped rows would satisfy
    monotonicity and be badly wrong.

    Treating `order_by` as a refinement by symmetry with `limit` is the category
    error behind #294, where appending keys makes a caller's sort a silent
    no-op.

    `with_annotations` was the second preserving clause and went with
    `CodeQuery` (#306): `collect()` hydrates whole artefacts, so what is
    loaded is no longer a query clause and there is nothing left to preserve.
    """
    _ = data

    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        rows = present(reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))).codes
        before = set(rows.ids())
        assert set(rows.order_by(q.code.id).ids()) == before
        assert set(rows.order_by(q.code.depth.desc()).ids()) == before


# ── The same laws over a second root ──────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(h=forest_hierarchies(), data=st.data())
def test_the_hierarchy_dsl_obeys_the_same_laws(h: Hierarchy, data: st.DataObject):
    """The laws over `q.hcode`, a root with its own tables and joins.

    There was a separate `HierarchyNodeQuery` with its own compile path until
    #306 folded the four DSLs into one `Query`; the laws are still checked over
    a second root, because a root's own constraint and join plan are where a
    narrowing clause could be lost.
    """
    conn = create_schema(duckdb.connect(":memory:"))
    try:
        put_hierarchy(conn, h)
        urn = str(
            SdmxUrn.make(
                Hierarchy,
                package=h.sdmx_package,
                artefact_class=h.sdmx_class,
                agency=h.agency_id,
                id=h.id,
                version=h.version,
            )
        )
        lazy = get_artefact(conn, urn)
        rows = present(lazy).nodes
        everything = rows.ids()
        n = _bounded(data, len(everything))
        target = data.draw(st.sampled_from(everything or ["x"]), label="target")
        pred = q.hcode.id != target

        # Monotone, idempotent, commuting — same three laws.
        assert set(rows.filter(pred).ids()) <= set(everything)
        assert set(rows.limit(n).ids()) <= set(everything)
        assert set(rows.offset(n).ids()) <= set(everything)
        assert rows.limit(n).limit(n).ids() == rows.limit(n).ids()
        assert rows.offset(n).offset(n).ids() == rows.offset(n).ids()
        assert _apply(rows, lambda x: x.filter(pred), lambda x: x.limit(n)) is not None
        assert _apply(rows, lambda x: x.limit(n), lambda x: x.filter(pred)) is None, "guard not wired here"
        # And the same carve-out.
        assert rows.offset(n).limit(n).ids() == everything[n : n + n]
        assert rows.limit(n).offset(n).ids() == everything[:n][n:]
        # Ordering preserves membership.
        assert set(rows.order_by(q.hcode.id).ids()) == set(everything)
    finally:
        conn.close()


# ── And on the artefact spine, whose rows are whole artefacts ────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(
    cls=st.lists(
        annotated_forest_codelists(), min_size=1, max_size=4, unique_by=lambda c: (c.agency_id, c.id, c.version)
    ),
    data=st.data(),
)
def test_the_artefact_spine_obeys_the_same_laws(cls: list[Codelist], data: st.DataObject):
    """A third query class, over a different relation and a different row shape."""
    with LocalRegistry(":memory:") as reg:
        for cl in cls:
            reg.add(cl)
        rows = reg.artefacts(sl.Codelist)
        everything = rows.order_by(q.artefact.urn).ids()
        n = _bounded(data, len(everything))
        agency = data.draw(st.sampled_from([c.agency_id for c in cls]), label="agency")
        pred = q.artefact.agency == agency

        assert set(rows.filter(pred).ids()) <= set(everything)
        assert rows.filter(pred).filter(pred).ids() == rows.filter(pred).ids()
        assert rows.limit(n).limit(n).ids() == rows.limit(n).ids()
        assert rows.offset(n).offset(n).ids() == rows.offset(n).ids()
        assert _apply(rows, lambda x: x.filter(pred), lambda x: x.limit(n)) is not None
        assert _apply(rows, lambda x: x.limit(n), lambda x: x.filter(pred)) is None, "guard not wired here"
        assert set(rows.order_by(q.artefact.urn).ids()) == set(everything)


# ── The guard against the class, not the instances ───────────────────────────


def test_every_composition_method_is_classified() -> None:
    """A new composition method must be assigned a law, or this fails.

    The part that guards the *class*. Without it the properties above only
    cover the clauses someone remembered to add, and the next `limit`-shaped
    method arrives untested — which is exactly how #264's window defect would
    have shipped.

    One class since #306, where four DSLs became `Query[R]`. That makes the
    guard stronger rather than weaker: the composition surface is declared in
    one place, so "found" is the whole of it, and a `Self`-returning method
    added for one root is added for every root at once.
    """
    import inspect

    from sdmxlib.query.query import Query

    #: Method -> which law governs it. Every composition on the query class
    #: must appear. "row-set" gets monotonicity, idempotence and (pairwise)
    #: commutativity; "preserving" changes something other than membership.
    classified = {
        "filter": "row-set",
        "qualify": "row-set",
        "limit": "row-set",
        "offset": "row-set",
        "intersect": "row-set",
        "difference": "row-set",
        "after": "row-set",
        "order_by": "preserving",
    }

    found = {
        name
        for name, member in vars(Query).items()
        if not name.startswith("_")
        and callable(member)
        and "Self" in str(inspect.signature(member, annotation_format=annotationlib.Format.STRING).return_annotation)
    }

    unclassified = found - classified.keys()
    assert not unclassified, (
        f"unclassified composition methods: {sorted(unclassified)}. "
        "Decide whether the method refines the row set or preserves it, add it "
        "to `classified`, and extend the matching property."
    )
    stale = classified.keys() - found
    assert not stale, f"classified methods that no longer exist: {sorted(stale)}"
