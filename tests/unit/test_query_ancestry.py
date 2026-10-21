"""`ancestor_ids` — ancestry as a path, because it was never recursive.

The design's corollary is that recursion stays a named domain verb and the
compiler never emits a recursive CTE. That reads like a restriction and costs
almost nothing, because **the transitive SKOS verbs were not recursive at query
time either**: the writer bakes each row's ancestors into a list column, and
`narrower_transitive` compiles to `? = ANY(ancestor_ids)` — one containment
test against a column.

So exposing the column makes "everything under X" an ordinary predicate, on all
four relations that bake it at once. The tests below check it against the verb
it replaces, including the depth bound, which is `& (depth <= n)` rather than
anything new.

The direct edge came later and for a structural reason. `code.parent_id`
references `code.code_id` **within one codelist** — the reference is
`(codelist_urn, parent_id) -> (codelist_urn, code_id)`, composite — and `Edge`
carried a single column, so declaring it on `code_id` alone would have matched
across codelists: a wrong answer rather than a missing feature. Composite
columns fixed that, and `parent` / `children` are now ordinary paths beside
`ancestors` / `descendants`. See `docs/design/query-tree-paths.md` for how the
four relate, and why `parent_id` survives beside `parent.id`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.category import Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import Agency
from sdmxlib.query._edges import EDGES
from sdmxlib.query._expr import Pred
from sdmxlib.query.accessors import CodeRow, q
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sdmxlib.storage.lazy import LazyCodelist

pytestmark = pytest.mark.unit

_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=ESTAT:CL_GEO(1.0)"


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """A three-deep tree with a branch, in two codelists.

    The branch (`MX` beside `CA`) is what tells "under W" from "child of W".
    The second codelist repeats the ids, because ancestry read off a column
    with no codelist scoping would silently merge the two trees.
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
        registry.add(
            Codelist(
                id="CL_OTHER",
                maintainer=Agency(id="OECD"),
                name={"en": "Other"},
                codes=[Code(id="W", name={"en": "Whatever"}), Code(id="ON", name={"en": "Onwards"}, parent_id="W")],
            )
        )
        yield registry


def _lazy(reg: sl.LocalRegistry) -> LazyCodelist:
    return present(reg.codelist(_URN))


# ── ancestry, as a predicate ──────────────────────────────────────────────


def test_everything_under_a_node(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.code).filter(
        q.code.codelist.id == "CL_GEO",
        q.code.ancestors.id == "W",
    )
    assert sorted(found.ids()) == ["CA", "MX", "ON"]


def test_it_is_transitive_not_just_children(reg: sl.LocalRegistry) -> None:
    """`ON` is a grandchild. A `parent_id == "W"` predicate would miss it, and
    a fixture without a third level could not tell the two apart."""
    base = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO")
    assert sorted(base.filter(q.code.parent.id == "W").ids()) == ["CA", "MX"]
    assert sorted(base.filter(q.code.ancestors.id == "W").ids()) == ["CA", "MX", "ON"]


def test_the_node_itself_is_excluded(reg: sl.LocalRegistry) -> None:
    """A row is not its own ancestor, matching the verb's `include_self=False`
    default."""
    found = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO", q.code.ancestors.id == "W")
    assert "W" not in found.ids()


def test_a_depth_bound_is_an_ordinary_conjunction(reg: sl.LocalRegistry) -> None:
    """What `narrower_transitive(depth=1)` did, spelled with `&` rather than
    with a keyword argument the compiler has to know about."""
    found = reg.query(q.code).filter(
        q.code.codelist.id == "CL_GEO",
        (q.code.ancestors.id == "W") & (q.code.depth <= 1),
    )
    assert sorted(found.ids()) == ["CA", "MX"]


def test_it_negates(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO", ~(q.code.ancestors.id == "CA"))
    assert sorted(found.ids()) == ["CA", "MX", "W"]


def test_a_root_has_no_ancestors(reg: sl.LocalRegistry) -> None:
    base = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO")
    assert base.filter(q.code.ancestors.id == "CA").ids() == ["ON"]
    assert base.filter(q.code.ancestors.id == "ON").ids() == []


def test_ancestry_does_not_cross_codelists(reg: sl.LocalRegistry) -> None:
    """Both codelists have a `W` with descendants. The column stores ids, not
    urns, so without the codelist predicate this merges two trees — which is
    the fixture's whole reason for having a second one."""
    everything = reg.query(q.code).filter(q.code.ancestors.id == "W")
    assert sorted(everything.ids()) == ["CA", "MX", "ON", "ON"]
    scoped = everything.filter(q.code.codelist.id == "CL_GEO")
    assert sorted(scoped.ids()) == ["CA", "MX", "ON"]


# ── it agrees with the verb it replaces ───────────────────────────────────


def test_it_agrees_with_narrower_transitive(reg: sl.LocalRegistry) -> None:
    """The differential check that matters: the same question, two compilers."""
    lazy = _lazy(reg)
    for target in ("W", "CA", "ON"):
        verb = sorted(c.id for c in lazy.narrower_transitive(target).collect())
        path = sorted(reg.query(q.code).filter(q.code.codelist.id == "CL_GEO", q.code.ancestors.id == target).ids())
        assert verb == path, target


def test_it_agrees_with_narrower_transitive_at_a_depth(reg: sl.LocalRegistry) -> None:
    lazy = _lazy(reg)
    verb = sorted(c.id for c in lazy.narrower_transitive("W", depth=1).collect())
    path = sorted(
        reg.query(q.code)
        .filter(q.code.codelist.id == "CL_GEO", (q.code.ancestors.id == "W") & (q.code.depth <= 1))
        .ids()
    )
    assert verb == path == ["CA", "MX"]


# ── every relation that bakes it ──────────────────────────────────────────


def test_categories_carry_ancestry_too(reg: sl.LocalRegistry) -> None:
    reg.add(
        CategoryScheme(
            id="CS",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Subjects"},
            categories=[
                Category(
                    id="ECON",
                    name={"en": "Economy"},
                    categories=[Category(id="PRICES", name={"en": "Prices"})],
                )
            ],
        )
    )
    assert reg.query(q.category).filter(q.category.ancestors.id == "ECON").ids() == ["PRICES"]


def test_hierarchy_nodes_carry_ancestry_too(reg: sl.LocalRegistry) -> None:
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
    assert reg.query(q.hcode).filter(q.hcode.ancestors.id == "TOP").ids() == ["MID"]


# ── the shape of the operator ─────────────────────────────────────────────


def test_it_is_not_is_in(reg: sl.LocalRegistry) -> None:
    """`is_in` asks whether a scalar column is among caller-supplied values;
    `has` asks whether a caller-supplied value is among a stored list. Opposite
    directions, and the fixture answers them differently."""
    base = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO")
    assert sorted(base.filter(q.code.id.is_in(["W", "CA"])).ids()) == ["CA", "W"]
    assert sorted(base.filter(q.code.ancestors.id == "CA").ids()) == ["ON"]


def test_it_binds_its_value(reg: sl.LocalRegistry) -> None:
    compiled = reg.query(q.code).filter(q.code.ancestors.id == "'; DROP TABLE sdmx.code; --").to_sql()
    assert "DROP TABLE" not in compiled.sql
    assert "= ANY(" in compiled.sql
    assert "'; DROP TABLE sdmx.code; --" in compiled.params


def test_ancestor_ids_are_referentially_intact(reg: sl.LocalRegistry) -> None:
    """Every id in a baked list is a live key *in the same scope*.

    The invariant the #349 peephole rests on, and it was load-bearing before
    that rewrite existed without anything asserting it. `? = ANY(ancestor_ids)`
    drops two things the correlated `EXISTS` checked — that the ancestor row
    exists, and that it shares the scope column — and both come back as this
    one property of the writer.

    Derived from `EDGES` rather than hand-listed, so a membership edge added
    later is covered here on the day it is declared rather than the day
    someone remembers this test.

    Every one of the five relations is populated, and the row count asserted
    before the dangling count. An empty table satisfies "no dangling ids"
    vacuously, which would read as coverage of `concept` and
    `metadata_attribute` while testing neither — the failure mode
    `forest_hierarchies` populates its fields to avoid.
    """
    reg.add(
        ConceptScheme(
            id="CS_CON",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Concepts"},
            concepts=[
                Concept(id="GEO", name={"en": "Geo"}),
                Concept(id="CITY", name={"en": "City"}, parent_id="GEO"),
            ],
        )
    )
    reg.add(
        MetadataStructure(
            id="MSD",
            maintainer=Agency(id="ESTAT"),
            name={"en": "MSD"},
            attributes=[
                MetadataAttribute(
                    id="CONTACT",
                    concept=concept_ref("CONTACT"),
                    children=[MetadataAttribute(id="EMAIL", concept=concept_ref("EMAIL"))],
                )
            ],
        )
    )
    reg.add(
        CategoryScheme(
            id="CS",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Subjects"},
            categories=[
                Category(id="ECON", name={"en": "Economy"}, categories=[Category(id="PRICES", name={"en": "P"})])
            ],
        )
    )
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
    membership = [edge for edge in EDGES if edge.member_of is not None]
    assert membership, "no membership edges — the peephole has nothing to guard"
    checked = 0
    for edge in membership:
        (scope, _), (listed, key) = edge.columns[0], edge.columns[edge.member_of or 0]
        listed_ids = reg.sql(
            f"SELECT count(*) AS n FROM sdmx.{edge.source} r, unnest(r.{listed}) AS a(value)"
        ).collect()["n"][0]
        assert listed_ids > 0, f"{edge.source}: nothing baked, so the check below is vacuous"
        dangling = reg.sql(
            f"SELECT count(*) AS n FROM sdmx.{edge.source} r, unnest(r.{listed}) AS a(value) "
            f"WHERE NOT EXISTS (SELECT 1 FROM sdmx.{edge.target} t "
            f"WHERE t.{scope} = r.{scope} AND t.{key} = a.value)"
        )
        assert dangling.collect()["n"].to_list() == [0], edge.source
        checked += 1
    assert checked == len(membership)


def test_an_equality_on_the_joined_key_emits_no_subquery(reg: sl.LocalRegistry) -> None:
    """The #349 peephole. `ancestors.id == X` asks the far side for nothing
    but the key it was joined on, so the correlated `EXISTS` joins a table
    solely to assert that a constant appears in a list the outer row already
    carries. Measured on the census bundle, dropping it is 6.8x on `.ids()`.

    Asserted on the emitted SQL rather than on wall time, per CLAUDE.md's
    caching rule: the guard is *which shape we emit*, and a timing assertion
    would go flaky without ever saying that.
    """
    compiled = reg.query(q.code).filter(q.code.ancestors.id == "W").to_sql()
    assert "EXISTS" not in compiled.sql
    assert "? = ANY(s0.ancestor_ids)" in compiled.sql


@pytest.mark.parametrize(
    ("label", "predicate"),
    [
        # asks the far side for a column that is not the joined key
        ("a non-key column", q.code.ancestors.name["en"] == "World"),
        # not an equality, so `= ANY` is not the same question
        ("a non-equality", q.code.ancestors.id != "W"),
        ("a set membership", q.code.ancestors.id.is_in(["W", "CA"])),
        # the reverse direction reads the list off the *inner* row, where no
        # rewrite on the outer alias can reach it
        ("the reverse hop", q.code.descendants.id == "W"),
        # to-many, but an ordinary composite FK — there is no list on the
        # outer row for the constant to be tested against
        ("a non-membership fan-out", q.code.children.id == "W"),
    ],
)
def test_the_peephole_does_not_fire_where_it_would_change_the_answer(
    reg: sl.LocalRegistry, label: str, predicate: Pred[CodeRow]
) -> None:
    """Each of these keeps its join, and each would answer differently without
    it. A guard that silently widened would show up here rather than as a
    wrong result somewhere downstream."""
    compiled = reg.query(q.code).filter(predicate).to_sql()
    assert "EXISTS" in compiled.sql, label


def test_the_collapsed_form_agrees_with_the_join_it_replaces(reg: sl.LocalRegistry) -> None:
    """The rewrite's own differential check, run against the SQL it replaced.

    `test_ancestry_agrees_with_a_fresh_traversal` already holds the *meaning*
    to the oracle. This holds the *rewrite* to the form it dropped, which is
    the narrower question and the one that regresses if a guard moves.
    """
    join_form = (
        "SELECT s0.code_id FROM sdmx.code s0 "
        "WHERE EXISTS (SELECT 1 FROM sdmx.code s1 "
        "WHERE s1.codelist_urn = s0.codelist_urn AND s1.code_id = ANY(s0.ancestor_ids) "
        "AND s1.code_id = $target) ORDER BY s0.code_urn"
    )
    for target in ("W", "CA", "ON", "MX", "nonexistent"):
        expected = sorted(reg.sql(join_form, target=target).collect()["code_id"].to_list())
        assert sorted(reg.query(q.code).filter(q.code.ancestors.id == target).ids()) == expected, target


def test_the_self_reference_is_declared_composite(reg: sl.LocalRegistry) -> None:
    """It could not be, until `Edge` grew composite columns.

    `code.parent_id` references `code.code_id` *within one codelist*, so a
    single-column edge would match across codelists — a wrong answer rather
    than a missing feature. The fixture's second codelist repeats `W`, so a
    dropped scope shows up here as extra rows.
    """
    geo = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO")
    assert sorted(geo.filter(q.code.children.id == "CA").ids()) == ["W"]
    assert sorted(geo.filter(q.code.parent.id == "W").ids()) == ["CA", "MX"]
    # unscoped, the two codelists' `W`s stay apart because the edge carries
    # `codelist_urn` as well as the id
    assert sorted(reg.query(q.code).filter(q.code.children.id == "ON").ids()) == ["CA", "W"]
