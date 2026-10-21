"""Resolving many rows registers the key set instead of binding it.

`Query.collect()` over 10,000 codes took **1.24 s**, against 22 ms for the
same rows through `CodeQuery.collect()`. Neither the SQL nor the object
construction accounted for it: profiling showed 50,006 `importlib` lookups —
five per row — all of them DuckDB's client probing for pandas on every bound
value. The cost is the binding, and it is the same ~48 µs per value that
`Session.value_set` exists to avoid and that `docs/cookbook/discovery.md`
quotes at callers. It is that large only with pandas absent — DuckDB never
remembers the failed import — which a later re-measure missed by running with
pandas importable and concluding DuckDB had got 25x faster (#411).

The library was not taking its own advice. `_resolve_code_keys` and
`fetch_annotations_for` both passed the whole key set as parameters.

    n        = ANY(?)      registered
    1          0.26 ms       0.41 ms
   10          0.71 ms       0.41 ms
  100          5.34 ms       0.64 ms
 2000         99.16 ms       0.85 ms
10000        494.12 ms       2.10 ms

Measured end to end over 10,000 codes:

    OLD collect()              22.2 ms  ->  22.2 ms
    OLD with_annotations("*")  766.8 ms ->  40.3 ms
    NEW Query.collect()       1238.1 ms ->  33.6 ms

That last row is why this blocked the §3 retirement: `Query.collect()` always
returns *complete* objects, so it could not be swapped in while it was 58×
slower than the query it replaces. It is now faster than the old eager form.

It also removes the reason `with_annotations` existed. Annotations were opt-in
because loading them cost 745 ms per 10k codes; they cost ~11 ms now, and a
`Code` with silently empty annotations is a worse thing to hand a caller than
one that is 11 ms slower.

These tests assert the *shape* — that the set is registered, and that the
result is right at both sides of the threshold — rather than wall-clock, per
CLAUDE.md: guard the fix with a count, not a timing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query.accessors import q
from sdmxlib.session import _REGISTER_ABOVE, value_match
from tests.support.factories import code_urn
from tests.support.narrow import present

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=ESTAT:CL(1.0)"


def _codelist(n: int) -> Codelist:
    return Codelist(
        id="CL",
        maintainer=Agency(id="ESTAT"),
        name={"en": "n"},
        codes=[
            Code(
                id=f"C{i}",
                name={"en": f"c{i}"},
                annotations=[Annotation(id=f"a{i}", type="tier", title="core")] if i % 2 == 0 else [],
            )
            for i in range(n)
        ],
    )


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        yield registry


# ── the helper picks the cheaper form ─────────────────────────────────────


def test_a_handful_is_bound(reg: sl.LocalRegistry) -> None:
    """Registration costs ~0.4 ms flat, so it is not worth it for two or three
    literals. That is the only reason the bind path survives."""
    cur = reg._read_cursor()
    with value_match(cur, ["a", "b"]) as (fragment, params):
        assert fragment == "= ANY(?)"
        assert params == [["a", "b"]]


def test_a_real_set_is_registered(reg: sl.LocalRegistry) -> None:
    cur = reg._read_cursor()
    values = [f"u{i}" for i in range(_REGISTER_ABOVE + 1)]
    with value_match(cur, values) as (fragment, params):
        assert fragment.startswith("IN (SELECT v FROM ")
        assert params == []


def test_the_relation_does_not_outlive_the_block(reg: sl.LocalRegistry) -> None:
    """A registration that leaks is session state nobody owns — the reason
    `Session.value_set` is a context manager too."""
    cur = reg._read_cursor()
    values = [f"u{i}" for i in range(_REGISTER_ABOVE + 1)]
    with value_match(cur, values) as (fragment, _):
        name = fragment.removeprefix("IN (SELECT v FROM ").removesuffix(")")
        counted = cur.execute(f"SELECT count(*) FROM {name}").fetchone()
        assert counted is not None
        assert counted[0] == len(values)
    with pytest.raises(Exception, match=name):
        cur.execute(f"SELECT count(*) FROM {name}").fetchone()


def test_the_column_type_is_not_forced_to_text(reg: sl.LocalRegistry) -> None:
    """Annotation ids are BIGINT. Coercing them to text raised a binder error
    rather than returning wrong rows — the good failure, but a failure, and
    the reason the helper lets pyarrow infer."""
    cur = reg._read_cursor()
    with value_match(cur, list(range(_REGISTER_ABOVE + 5))) as (fragment, _):
        name = fragment.removeprefix("IN (SELECT v FROM ").removesuffix(")")
        typed = cur.execute(f"SELECT typeof(v) FROM {name} LIMIT 1").fetchone()
        assert typed is not None
        kind = str(typed[0])
    assert "INT" in kind.upper()


# ── and the answers are the same on both sides of the threshold ───────────


@pytest.mark.parametrize("n", [_REGISTER_ABOVE - 1, _REGISTER_ABOVE, _REGISTER_ABOVE + 1, 200])
def test_collect_agrees_across_the_threshold(reg: sl.LocalRegistry, n: int) -> None:
    """The switch is an optimisation, so it must be invisible. Sizes chosen to
    straddle the boundary, because an off-by-one there would show only at one
    exact length."""
    reg.add(_codelist(n))
    found = reg.query(q.code).filter(q.code.codelist.urn == _URN).collect()
    assert {c.id for c in found} == {f"C{i}" for i in range(n)}
    annotated = {c.id for c in found if c.annotations}
    assert annotated == {f"C{i}" for i in range(0, n, 2)}


def test_collect_still_returns_complete_objects(reg: sl.LocalRegistry) -> None:
    """The property that made the cost unavoidable, and is now cheap.

    `CodeQuery.collect()` returned a `Code` whose `.annotations` was empty even
    when the store had them — opt-in via `with_annotations`. The path language
    has no such mode, because it no longer needs one.
    """
    reg.add(_codelist(200))
    found = reg.query(q.code).filter(q.code.id == "C0").collect()
    assert [a.type for a in found[0].annotations] == ["tier"]


def test_the_lazy_collection_matches_the_direct_query_code_for_code(reg: sl.LocalRegistry) -> None:
    """Two routes to the same codes: `codelist.codes` and a query on `q.code`.

    Both are `Query[CodeRow, Code]` since #306, so this is no longer a differential
    between two DSLs — it is the claim that reaching a relation through a lazy
    artefact adds nothing but a filter and an order.

    Compared as a mapping rather than a sequence, because the two disagree on
    *order* by design and that is worth stating rather than hiding.
    `codelist.codes` orders by `position`, matching declaration order and the
    eager `ItemList`; a bare query appends only its `__key__` tiebreak, which
    for a code is `code_urn`, so `C10` sorts before `C2`. A caller who wants
    declaration order from the bare query asks for it.
    """
    reg.add(_codelist(200))
    old = present(reg.codelist(_URN)).codes.collect()
    new = reg.query(q.code).filter(q.code.codelist.urn == _URN).collect()

    def by_id(codes: Sequence[Code]) -> dict[str, list[str]]:
        return {c.id: sorted(a.type for a in c.annotations if a.type is not None) for c in codes}

    assert by_id(old) == by_id(new)
    assert [c.id for c in old] != [c.id for c in new], "the orders coincided — the claim above is untested"
    ordered = reg.query(q.code).filter(q.code.codelist.urn == _URN).order_by(q.code.position).collect()
    assert [c.id for c in ordered] == [c.id for c in old]


# ── every root that can build an object, does ─────────────────────────────


def test_hierarchy_nodes_resolve(reg: sl.LocalRegistry) -> None:
    """`q.hcode.collect()` refused outright while `h.nodes.collect()` worked —
    the path language simply had no resolver for that relation. It blocked
    repointing `LazyHierarchy.nodes`, which is the §3 step after this one.
    """
    from sdmxlib.model.annotations import Annotation as Ann
    from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level

    level = Level(id="L1", name={"en": "one"})
    reg.add(
        Hierarchy(
            id="H",
            maintainer=Agency(id="ESTAT"),
            name={"en": "H"},
            levels=[level],
            tree=[
                HierarchicalCode(
                    id="TOP",
                    code=code_urn("CL", "TOP"),
                    level=level,
                    annotations=[Ann(id="a", type="t", title="x")],
                    children=[HierarchicalCode(id="MID", code=code_urn("CL", "MID"))],
                )
            ],
        )
    )
    found = {n.id: n for n in reg.query(q.hcode).collect()}
    assert sorted(found) == ["MID", "TOP"]
    assert found["TOP"].level is not None
    assert found["TOP"].level.id == "L1"
    assert [a.type for a in found["TOP"].annotations] == ["t"]
    assert found["MID"].level is None


def test_it_matches_the_old_hierarchy_dsl(reg: sl.LocalRegistry) -> None:
    """The differential, while both exist."""
    from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy

    reg.add(
        Hierarchy(
            id="H",
            maintainer=Agency(id="ESTAT"),
            name={"en": "H"},
            tree=[
                HierarchicalCode(
                    id="TOP",
                    code=code_urn("CL", "TOP"),
                    children=[HierarchicalCode(id="MID", code=code_urn("CL", "MID"))],
                )
            ],
        )
    )
    node_urn = Hierarchy.urn_for(agency="ESTAT", id="H", version="1.0")
    old = {n.id: n for n in present(reg.get(node_urn)).nodes.collect()}
    new = {n.id: n for n in reg.query(q.hcode).collect()}
    assert sorted(old) == sorted(new) == ["MID", "TOP"]
    assert all(old[i].code == new[i].code and old[i].level == new[i].level for i in old)


def test_a_placements_children_are_a_query_not_a_field(reg: sl.LocalRegistry) -> None:
    """`children` is left empty by the resolver, deliberately.

    A placement's children are `q.hcode.children` — materialising the subtree
    per row would make `collect()` quadratic on the shape it is most used for.
    `LazyHierarchy.materialize()` is what builds the linked tree.
    """
    from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy

    reg.add(
        Hierarchy(
            id="H",
            maintainer=Agency(id="ESTAT"),
            name={"en": "H"},
            tree=[
                HierarchicalCode(
                    id="TOP",
                    code=code_urn("CL", "TOP"),
                    children=[HierarchicalCode(id="MID", code=code_urn("CL", "MID"))],
                )
            ],
        )
    )
    top = next(n for n in reg.query(q.hcode).collect() if n.id == "TOP")
    assert list(top.children) == []
    assert reg.query(q.hcode).filter(q.hcode.parent.id == "TOP").ids() == ["MID"]
