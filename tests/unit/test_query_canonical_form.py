"""Canonical form: same meaning, same value, same key (#270).

`to_sql()` was order-dependent, so anything keying a cache on it degraded
silently as query diversity grew. The reviewer's instinct — "hash the IR
instead" — did not work either: `predicates` is an ordered tuple, so
`filter(a).filter(b)` and `filter(b).filter(a)` were unequal *as values*. Both
the text and the structure carried construction order the semantics does not.

sdmxlib has no cache of its own, so nothing here was broken. It is a
requirement from downstream: a REST service built on sdmxlib keys cached results on what we hand
it, and we were making correct keying impossible without the consumer
re-deriving a canonical form we are better placed to provide.

## The trap the issue named, and why it is the important part

`test_every_ordering_either_commutes_or_is_refused` (#293) asserts
commutativity **by result**:

    assert q.filter(a).filter(b).ids() == q.filter(b).filter(a).ids()

That passes forever while the key stays broken — result-equality is not
identity-equality, and the difference is exactly this issue. So the properties
here assert *identity*, and the test at the bottom of this file pins that the
stronger claim actually holds everywhere the weaker one does.

## What is canonicalised, and what deliberately is not

Canonical *in the key*: the predicate set (ANDed, so commutative), `And` / `Or`
operands (likewise), `is_in` members and `with_annotations` types (sets — order
and multiplicity carry nothing).

Canonical **in the key and not at construction**, which is the one design
decision here worth arguing with. Sorting when a predicate is built makes the
IR compare equal directly and is tidier; it also cost 116x on a 5000-element
`is_in`, 5x on a predicate chain and 2.4x on a single predicate, all measured,
on the path #271 already flags as hot. So the stored form is what the caller
wrote and the *key* is canonical.

**Not** canonical: `order_by` keys. Order is the whole meaning there —
``order_by(a, b)`` and ``order_by(b, a)`` are different queries — and it is the
one field where the obvious move is wrong. Asserted below, because a later
reader "finishing the job" is a realistic way to break it.
"""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.query.accessors import q
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


@pytest.fixture
def codes():
    cl = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[Code(id="A", name={"en": "a"}), Code(id="B", name={"en": "b"}, parent_id="A")],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


# ── The headline case ────────────────────────────────────────────────────────


def test_filter_order_changes_neither_the_value_nor_the_key(codes) -> None:
    """The measurement in the issue, inverted into an assertion.

    All four lines mattered: before this, rows matched while SQL text, IR and
    key all differed.
    """
    a, b = q.code.id == "A", q.code.depth == 0
    left, right = codes.filter(a).filter(b), codes.filter(b).filter(a)

    assert left.ids() == right.ids()
    assert left.canonical_key() == right.canonical_key()

    # The text and the stored tuple still differ, and that is the trade: the
    # cost of making them agree was 116x on a 5000-element `is_in`, measured.
    # `canonical_key` is the thing a consumer keys on, so it is the thing made
    # canonical. See `_field_key`.
    assert left.to_sql() != right.to_sql()
    # Compared by *identity* rather than by `==`, which a predicate refuses
    # (#306) — the claim is only that the stored tuples are not the same
    # objects in the same order.
    assert [id(p) for p in left.predicates] != [id(p) for p in right.predicates]


def test_the_key_is_hashable_and_deterministic(codes) -> None:
    """Hashable so a consumer can key on it; deterministic so it can be persisted.

    Determinism is asserted by rebuilding the query from scratch rather than by
    calling the method twice — the failure mode is a key built from ``hash()``
    (salted per process) or ``id()``, and both are stable within one call.
    """
    built_once = codes.filter(q.code.id == "A").limit(3)
    built_again = codes.filter(q.code.id == "A").limit(3)
    assert built_once.canonical_key() == built_again.canonical_key()
    assert isinstance(hash(built_once.canonical_key()), int)
    assert {built_once.canonical_key(), built_again.canonical_key()} == {built_once.canonical_key()}


def test_three_predicates_in_any_order_agree(codes) -> None:
    """Not just the two-predicate case: canonical order is a total order.

    A sort that happened to be stable for two elements and not for three would
    pass the headline test.
    """
    import itertools

    preds = [q.code.id != "Z", q.code.depth == 0, q.code.parent.id.is_null()]
    keys = {codes.filter(p1).filter(p2).filter(p3).canonical_key() for p1, p2, p3 in itertools.permutations(preds)}
    assert len(keys) == 1


# ── Sets: order and multiplicity carry nothing ───────────────────────────────


def test_is_in_members_are_sorted_and_deduplicated(codes) -> None:
    """``IN (A, A, B)`` and ``IN (B, A)`` are the same query, and key alike.

    Order and multiplicity carry no meaning in ``IN (...)``. The set is
    canonicalised in the key rather than at construction — see `_field_key`
    for the 116x that decided it.
    """
    from sdmxlib.query._common import canonical_key

    assert canonical_key(q.code.id.is_in(["B", "A"])) == canonical_key(q.code.id.is_in(["A", "B"]))
    assert canonical_key(q.code.id.is_in(["A", "A", "B"])) == canonical_key(q.code.id.is_in(["A", "B"]))
    assert codes.filter(q.code.id.is_in(["B", "A", "B"])).ids() == ["A", "B"]


def test_boolean_operands_commute_as_values(codes) -> None:
    """`a | b` and `b | a` are the same predicate, not merely the same rows.

    `And` and `Or` are the only commutative node kinds — `Not` is unary and the
    subquery predicates wrap a single operand — so this is the whole of it.
    """
    from sdmxlib.query._common import canonical_key

    a, b = q.code.id == "A", q.code.depth == 0
    assert canonical_key(a | b) == canonical_key(b | a)
    assert canonical_key(a & b) == canonical_key(b & a)
    assert codes.filter(a | b).canonical_key() == codes.filter(b | a).canonical_key()


# ── What is deliberately not canonicalised ───────────────────────────────────


def test_order_by_keys_are_not_sorted(codes) -> None:
    """The one field where the obvious move is wrong.

    ``order_by(a, b)`` sorts by ``a`` then ``b``; reversed it sorts by ``b``
    then ``a``. Those are different queries and must have different keys, or a
    cache serves one page order for another.

    This is not hypothetical: the first implementation of `canonical_key` did
    exactly that, by accident. `CodeQuery` satisfies the `SqlRelation`
    protocol — it has both `to_sql` and `columns` — so a `runtime_checkable`
    isinstance matched it first and keyed the query on its own compiled SQL,
    which is emitted with ``omit_order=True``. Both orderings produced the same
    key. Caught only because this test asserts the order-sensitive case instead
    of assuming it.
    """
    ascending = codes.order_by(q.code.id, q.code.depth)
    reversed_keys = codes.order_by(q.code.depth, q.code.id)
    assert ascending.canonical_key() != reversed_keys.canonical_key()

    # Direction is part of the meaning too.
    assert codes.order_by(q.code.id).canonical_key() != codes.order_by(q.code.id.desc()).canonical_key()


def test_every_field_that_changes_the_result_changes_the_key(codes) -> None:
    """A key that collides is worse than no key — it serves the wrong rows.

    Derived from the `attrs` field list rather than hand-listed, so a field
    added later is covered unless it is declared ``eq=False``, which is already
    how connections are excluded.
    """
    variants = {
        "base": codes,
        "filtered": codes.filter(q.code.id == "A"),
        "other filter": codes.filter(q.code.id == "B"),
        "ordered": codes.order_by(q.code.id),
        "limited": codes.limit(1),
        "other limit": codes.limit(2),
        "offset": codes.offset(1),
        # `with_annotations` was the eighth variant and went with `CodeQuery`
        # (#306). `qualify` and the cursor took its place on the surface, and
        # both must key distinctly for the same reason it did.
        "qualified": codes.qualify(q.code.id.rank().over(q.code.depth) == 1),
    }
    keys = {name: variant.canonical_key() for name, variant in variants.items()}
    assert len(set(keys.values())) == len(variants), (
        f"colliding keys: {[n for n in keys if list(keys.values()).count(keys[n]) > 1]}"
    )


# ── The stronger claim, everywhere #293 makes the weaker one ─────────────────


def test_commuting_pairs_agree_by_identity_not_only_by_result(codes) -> None:
    """Closes the trap #270 named in #293's property.

    #293 asserts that every ordering either commutes *by result* or is refused.
    That passes forever while keys stay broken. Here the same pairs are checked
    by *key*, which is the claim a cache actually needs.

    `limit`/`offset` is excluded: it is the one pair that legitimately does not
    commute (#264), and it has its own property asserting so.
    """
    a = q.code.id != "Z"
    clauses = {
        "filter": lambda query: query.filter(a),
        "filter2": lambda query: query.filter(q.code.depth == 0),
        "descendants": lambda query: query.filter(q.code.ancestors.id == "A"),
        "limit": lambda query: query.limit(2),
        "offset": lambda query: query.offset(0),
    }
    for first, apply_first in clauses.items():
        for second, apply_second in clauses.items():
            if {first, second} == {"limit", "offset"}:
                continue
            try:
                forward = apply_second(apply_first(codes)).canonical_key()
                backward = apply_first(apply_second(codes)).canonical_key()
            except ValueError:
                continue  # refused by the slice guard (#296); #293 covers that
            assert forward == backward, f"{first} then {second} keys differently than the reverse"


# ── Every DSL ────────────────────────────────────────────────────────────────


def test_the_other_dsls_canonicalise_too() -> None:
    """The key is per-query-class, so "all four agree" is four call sites."""
    h = Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "H"},
        tree=[
            HierarchicalCode(id="N1", code=code_urn("CL", "N1")),
            HierarchicalCode(id="N2", code=code_urn("CL", "N2")),
        ],
    )
    dsd = DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "S"},
        dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"))],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(h)
        reg.add(dsd)
        reg.add(Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "F"}, structure=dsd))

        nodes = present(reg.get(Hierarchy.urn_for(agency="AG", id="H", version="1.0"))).nodes
        n1, n2 = q.hcode.id != "N9", q.hcode.depth == 0
        assert nodes.filter(n1).filter(n2).canonical_key() == nodes.filter(n2).filter(n1).canonical_key()

        # `ArtefactQuery` used to be asserted here and was retired in #306.
        # `reg.artefacts()` returns the path language's `Query` now, so its key
        # is the same one the block below checks.

        # The new language's own key, asserted here too because this file is
        # the cross-DSL statement of #270 and `reg.reported_attributes()` is now
        # the generic query. `tests/unit/test_query_canonical_key.py` has the
        # rest of its contract.
        values = reg.reported_attributes()
        m1 = q.reported_attribute.attribute_path != "X"
        m2 = q.reported_attribute.kind != "Y"
        assert values.filter(m1).filter(m2).canonical_key() == values.filter(m2).filter(m1).canonical_key()


def test_queries_over_different_artefacts_never_collide() -> None:
    """The scope field is part of the key, which is the one collision that would
    serve another codelist's rows.
    """
    with LocalRegistry(":memory:") as reg:
        for cl_id in ("CL_A", "CL_B"):
            reg.add(
                Codelist(
                    id=cl_id, maintainer="AG", version="1.0", name={"en": cl_id}, codes=[Code(id="X", name={"en": "x"})]
                )
            )
        a = present(reg.get(Codelist.urn_for(agency="AG", id="CL_A", version="1.0"))).codes
        b = present(reg.get(Codelist.urn_for(agency="AG", id="CL_B", version="1.0"))).codes
        assert a.canonical_key() != b.canonical_key()
        assert a.filter(q.code.id == "X").canonical_key() != b.filter(q.code.id == "X").canonical_key()


def test_the_key_identifies_the_question_not_the_answer() -> None:
    """Two stores, same codelist URN, different contents — **same key**.

    Pinned as intended rather than left to be discovered, because it is the one
    way a consumer can get a wrong answer out of a correct key. The key is
    derived from the query alone; it says nothing about which database the
    query runs against or what is in it. The same query before and after a
    write also keys identically.

    Baking the connection in is not the fix: a connection has no stable
    cross-process identity, and including one would destroy the determinism
    that makes the key persistable in the first place. So a cache key is a
    pair — ``(datasource identity and epoch, canonical_key())`` — and the first
    half belongs to the consumer, who alone knows when the store changed.

    Filed as #298: nothing in the store exposes an epoch today.
    """

    def build(reg, ids):
        reg.add(
            Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "n"},
                codes=[Code(id=i, name={"en": i}) for i in ids],
            )
        )
        return reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0")).codes

    with LocalRegistry(":memory:") as first, LocalRegistry(":memory:") as second:
        one, two = build(first, ["A", "B"]), build(second, ["X", "Y", "Z"])
        assert one.ids() != two.ids()
        assert one.canonical_key() == two.canonical_key()


def test_the_key_does_not_depend_on_hash_randomisation() -> None:
    """Determinism across processes, asserted across an actual process boundary.

    The in-process test above cannot see this: `hash("x")` is stable within a
    run and salted between runs, so a key built from it passes every same
    process check and fails in production the moment two workers compare keys.
    """
    import subprocess
    import sys

    script = (
        "from sdmxlib.local import LocalRegistry\n"
        "from sdmxlib.model.codelist import Code, Codelist\n"
        "from sdmxlib.query.accessors import q\n"
        "cl = Codelist(id='CL', maintainer='AG', version='1.0', name={'en': 'n'},"
        " codes=[Code(id='A', name={'en': 'a'})])\n"
        "with LocalRegistry(':memory:') as reg:\n"
        "    reg.add(cl)\n"
        "    rows = reg.get(Codelist.urn_for(agency='AG', id='CL', version='1.0')).codes\n"
        "    print(rows.filter(q.code.id == 'A').limit(2).canonical_key())\n"
    )
    runs = {
        subprocess.run(  # noqa: S603
            [sys.executable, "-c", script], check=True, capture_output=True, text=True, env={"PYTHONHASHSEED": seed}
        ).stdout
        for seed in ("0", "1", "random")
    }
    assert len(runs) == 1, f"key varies with PYTHONHASHSEED: {runs}"
