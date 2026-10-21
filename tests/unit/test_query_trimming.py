"""Join trimming — `x.parent.id` reads the stored key, and emits no join.

A foreign key *is* the target's key, so resolving the row to read the key back
is work with no answer in it. `q.code.parent.id == "W"` compiles to
`WHERE s0.parent_id = ?`, with no `sdmx.code` joined to itself.

Django trims the same rewrite (`parent__id=1` never emits a join) and keeps a
`parent_id` field beside the relationship. **We do not**, and the difference is
the reason: Django's `parent_id` exists to be *assigned* — `obj.parent_id = 5`
saves fetching the related object on write. This is a read-only query language,
so the duplicate would offer a choice with no consequence attached, one
autocomplete from the other and indistinguishable in behaviour. Trimming makes
the hop free; removing the column means there is nothing to choose between.
`test_query_accessors.py::test_no_exposed_column_duplicates_a_declared_edge`
holds that line.

So these tests assert the compiled shape rather than comparing two spellings.
Asserting rows alone would pass on a compiler that trimmed nothing — on
referentially-intact data the join gives the same answer, which is exactly why
the missing rewrite was invisible for as long as it was.

What must *not* trim has its own section. A reverse hop has no stored key to
fall back on, a membership hop's source column is a list rather than one key,
and a non-key column on the far row is the reason the relationship exists at
all. Trimming any of those is a wrong answer rather than a slow one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.category import Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query._edges import EDGES
from sdmxlib.query._expr import Pred
from sdmxlib.query._joins import trim
from sdmxlib.query.accessors import CodeRow, q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Three deep with a branch, in two codelists.

    The second codelist repeats the ids so a trim that dropped the composite
    scope shows up as extra rows rather than as nothing at all.
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
                codes=[Code(id="W", name={"en": "Whatever"}), Code(id="ON", name={"en": "On"}, parent_id="W")],
            )
        )
        yield registry


# ── the hop reads the stored key ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "predicate"),
    [
        ("equality", q.code.parent.id == "W"),
        ("negation", ~(q.code.parent.id == "W")),
        ("is_null", q.code.parent.id.is_null()),
        ("is_not_null", q.code.parent.id.is_not_null()),
        ("is_in", q.code.parent.id.is_in(["W", "CA"])),
        ("str_match", q.code.parent.id.str.contains("A")),
    ],
)
def test_every_predicate_over_the_key_trims(reg: sl.LocalRegistry, name: str, predicate: Pred[CodeRow]) -> None:
    """Every operator, not just equality — the rewrite happens where the column
    is built, so an operator that trimmed differently would mean the rewrite
    had leaked into the compiler.

    Django is identical on the three of these it has (filter, exclude, isnull),
    verified against 6.1 rather than recalled. Negation is the one where a
    half-done trim shows, because `LEFT JOIN` plus `NOT` has different null
    behaviour from a column test.
    """
    sql = reg.query(q.code).filter(predicate).to_sql().sql
    assert "JOIN" not in sql, f"{name}: {sql}"
    assert "s0.parent_id" in sql, f"{name}: {sql}"


def test_the_trimmed_form_reads_the_right_column(reg: sl.LocalRegistry) -> None:
    """`s0.parent_id`, not `s0.code_id`. Dropping the hop without swapping the
    column reads the row's *own* id — a query that runs and is wrong."""
    sql = reg.query(q.code).filter(q.code.parent.id == "W").to_sql().sql
    assert "s0.parent_id = ?" in sql
    # Both codelists have a `W` with children, and the trimmed predicate is not
    # scoped to either — `ON` here is `CL_OTHER`'s.
    assert sorted(reg.query(q.code).filter(q.code.parent.id == "W").ids()) == ["CA", "MX", "ON"]
    scoped = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO", q.code.parent.id == "W")
    assert sorted(scoped.ids()) == ["CA", "MX"]


def test_the_key_has_exactly_one_spelling(reg: sl.LocalRegistry) -> None:
    """There is no `parent_id` column beside the hop, and that is deliberate.

    Trimming made the two identical in SQL, binds and `canonical_key`; the
    remaining difference was that a reader had to choose. Django keeps the pair
    because its `parent_id` is assignable, which a read-only language has no
    use for.
    """
    assert not hasattr(q.code, "parent_id")
    assert not hasattr(q.category, "parent_id")
    assert not hasattr(q.hcode, "parent_id")
    assert not hasattr(q.hcode, "level_id")


def test_trimming_repeats_until_it_cannot(reg: sl.LocalRegistry) -> None:
    """`parent.parent.id` sheds the inner hop too, leaving one join rather than
    two. Applied once it would leave both, which is why `trim` loops."""
    sql = reg.query(q.code).filter(q.code.parent.parent.id == "W").to_sql().sql
    assert sql.count("JOIN") == 1
    assert sorted(reg.query(q.code).filter(q.code.parent.parent.id == "W").ids()) == ["ON"]


def test_the_composite_scope_survives_the_trim(reg: sl.LocalRegistry) -> None:
    """`code.parent_id` references `code.code_id` *within one codelist*. The
    trim drops the hop, so the surviving predicate must still be scoped by the
    root row's own `codelist_urn` — which it is, trivially, because the root
    row is the only row left. The fixture's second `W` is what would show a
    trim that had somehow merged the two trees."""
    found = reg.query(q.code).filter(q.code.parent.id == "W")
    assert sorted(found.ids()) == ["CA", "MX", "ON"]
    scoped = found.filter(q.code.codelist.id == "CL_GEO")
    assert sorted(scoped.ids()) == ["CA", "MX"]


def test_it_trims_on_every_tree_relation(reg: sl.LocalRegistry) -> None:
    """The rule reads the edge graph, so it is not a codelist special case."""
    reg.add(
        CategoryScheme(
            id="CS",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Subjects"},
            categories=[
                Category(
                    id="ECON",
                    name={"en": "Economy"},
                    categories=[Category(id="PRICES", name={"en": "P"})],
                )
            ],
        )
    )
    sql = reg.query(q.category).filter(q.category.parent.nested_id == "ECON").to_sql().sql
    assert "JOIN" not in sql
    assert reg.query(q.category).filter(q.category.parent.nested_id == "ECON").ids() == ["PRICES"]


def test_a_nested_trees_parent_id_is_a_join(reg: sl.LocalRegistry) -> None:
    """Discrimination for the nested trees (#439): their parent edge joins on
    the *path*, so `parent.nested_id` is the joined key and trims, while the
    parent's leaf `id` is a column of the parent row and needs it."""
    reg.add(
        CategoryScheme(
            id="CS",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Subjects"},
            categories=[Category(id="ECON", name={"en": "E"}, categories=[Category(id="PRICES", name={"en": "P"})])],
        )
    )
    sql = reg.query(q.category).filter(q.category.parent.id == "ECON").to_sql().sql
    assert "JOIN sdmx.category" in sql
    assert reg.query(q.category).filter(q.category.parent.id == "ECON").ids() == ["PRICES"]


def test_it_trims_past_a_non_tree_edge(reg: sl.LocalRegistry) -> None:
    """Nothing about the rule is about trees — any to-one hop reaching the key
    it was reached by is redundant. A code's codelist is the ordinary case."""
    sql = reg.query(q.code).filter(q.code.codelist.urn == "no-such-urn").to_sql().sql
    assert "JOIN" not in sql


# ── what must not trim ────────────────────────────────────────────────────


def test_a_non_key_column_keeps_its_join(reg: sl.LocalRegistry) -> None:
    """The reason the relationship exists. Only the key is recoverable from the
    row; a label is not."""
    sql = reg.query(q.code).filter(q.code.parent.name["en"] == "World").to_sql().sql
    assert "LEFT JOIN sdmx.code" in sql
    assert sorted(reg.query(q.code).filter(q.code.parent.name["en"] == "World").ids()) == ["CA", "MX"]


def test_a_reverse_hop_does_not_trim(reg: sl.LocalRegistry) -> None:
    """`children` is to-many and its key lives on the *other* row, so there is
    nothing local to fall back to. It stays a correlated `EXISTS`."""
    sql = reg.query(q.code).filter(q.code.children.id == "CA").to_sql().sql
    assert "EXISTS" in sql
    assert sorted(reg.query(q.code).filter(q.code.children.id == "CA").ids()) == ["W"]


def test_a_membership_hop_does_not_trim(reg: sl.LocalRegistry) -> None:
    """`ancestors` matches by `= ANY(...)` against a stored *list*. Trimming it
    would compare a list column to a scalar — and `ancestors.id == "W"` would
    stop meaning "under W"."""
    sql = reg.query(q.code).filter(q.code.ancestors.id == "W").to_sql().sql
    assert "= ANY(" in sql
    found = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO", q.code.ancestors.id == "W")
    assert sorted(found.ids()) == ["CA", "MX", "ON"]


def test_the_two_directions_still_answer_differently(reg: sl.LocalRegistry) -> None:
    """The whole point of trimming only the forward direction. If it applied to
    both, these would collapse into each other."""
    geo = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO")
    assert sorted(geo.filter(q.code.parent.id == "W").ids()) == ["CA", "MX"]
    assert sorted(geo.filter(q.code.children.id == "W").ids()) == []
    assert sorted(geo.filter(q.code.descendants.id == "ON").ids()) == ["CA", "W"]


# ── why it resolves from the root rather than matching a name ─────────────


def test_a_forward_name_is_not_unique_on_its_own() -> None:
    """The reason `trim` takes a root table and walks `resolve_path`.

    A first version matched the edge by `(name, target, target_column)` and
    declined when that was ambiguous. It is ambiguous almost everywhere: six
    triples are shared, covering most edges that are not self-referential, so
    trimming fired on trees and nothing else — passing every tree test while
    doing nothing for `code.codelist.urn`. Declining safely is not the same as
    working.
    """
    shared: dict[tuple[str, str, str], list[str]] = {}
    for edge in EDGES:
        if edge.name is None:
            continue
        for _, target_column in edge.columns:
            shared.setdefault((edge.name, edge.target, target_column), []).append(edge.source)
    ambiguous = {key: sources for key, sources in shared.items() if len(sources) > 1}
    assert ambiguous, "if this is ever empty, name-matching would have been enough"
    assert ("codelist", "artefact", "urn") in ambiguous


def test_the_ambiguous_names_trim_anyway() -> None:
    """The payoff: `codelist` is declared on three source tables and all three
    resolve exactly, because the path is walked from a known root."""
    assert trim("code", ("codelist",), "artefact", "urn") == ((), "code", "codelist_urn")
    assert trim("dsd_component", ("codelist",), "artefact", "urn") == ((), "dsd_component", "codelist_urn")
    assert trim("concept", ("codelist",), "artefact", "urn") == ((), "concept", "codelist_urn")


def test_trim_declines_on_a_path_it_cannot_resolve() -> None:
    """Trimming runs at attribute-access time, so a bad path reaches it before
    the compiler does. Raising here would relocate `PathError` — which lists
    the reachable hops — to a worse place with a worse message, and it broke a
    shadowing test that legitimately builds an accessor on a synthetic route.
    """
    assert trim("code", ("no_such_hop",), "code", "code_id") == (("no_such_hop",), "code", "code_id")


def test_trim_rewrites_the_column_as_well_as_the_path() -> None:
    """Dropping the hop without swapping `code_id` for `parent_id` would read
    the root's *own* id — a query that runs, returns rows, and is wrong."""
    assert trim("code", ("parent",), "code", "code_id") == ((), "code", "parent_id")


def test_trim_leaves_an_empty_path_alone() -> None:
    assert trim("code", (), "code", "code_id") == ((), "code", "code_id")


def test_trim_stops_at_a_column_the_edge_does_not_carry() -> None:
    """`parent.name` is not the key the hop was reached by, so there is nothing
    to fall back to and the path survives for the planner to join."""
    assert trim("code", ("parent",), "code", "name") == (("parent",), "code", "name")


def test_the_accessor_carries_the_root_a_column_needs() -> None:
    """A hop accessor's `__table__` is where it *lands*; resolving its path
    needs where it *started*. Without `root` threaded through binding, a column
    at the end of a path could not be trimmed at all."""
    assert q.code.root == "code"
    assert q.code.parent.root == "code"
    assert q.code.codelist.root == "code"
    assert type(q.code.codelist).__table__ == "artefact"
