"""Paths resolve to declared hops, and hops plan into valid SQL.

The last test in this file is the one that matters most: the emitted `FROM`
clause is executed against the real schema. A join tree that type-checks and
deduplicates but names a column wrongly is worth nothing, and only the database
can say.
"""

from __future__ import annotations

import functools

import duckdb
import pytest

from sdmxlib.query._joins import Hop, Join, PathError, plan_joins, resolve_path
from sdmxlib.storage.schema import create_schema

pytestmark = pytest.mark.unit


@functools.cache
def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    create_schema(conn)
    return conn


# ── resolution ────────────────────────────────────────────────────────────


def test_a_single_forward_hop() -> None:
    (hop,) = resolve_path("dsd_component", ("dsd",))
    assert hop.name == "dsd"
    assert hop.forward is True
    assert hop.to_many is False
    assert hop.table == "artefact"


def test_two_hops_chain_through_the_intermediate_relation() -> None:
    first, second = resolve_path("dsd_component", ("concept", "codelist"))
    assert first.table == "concept"
    assert second.edge.source == "concept"
    assert second.edge.column == "codelist_urn"
    assert second.table == "artefact"


def test_an_inverse_hop_is_to_many() -> None:
    (hop,) = resolve_path("artefact", ("codes",))
    assert hop.forward is False
    assert hop.to_many is True
    assert hop.table == "code"


def test_a_facet_resolves_to_its_base_edge_plus_a_constant() -> None:
    (hop,) = resolve_path("artefact", ("dimensions",))
    assert hop.to_many is True
    assert hop.table == "dsd_component"
    assert hop.facet is not None
    assert hop.facet.column == "component_type"
    assert hop.facet.values == ("Dimension",)


def test_the_two_routes_to_a_codelist_resolve_differently() -> None:
    own = resolve_path("dsd_component", ("codelist",))
    default = resolve_path("dsd_component", ("concept", "codelist"))
    assert own[-1].edge.column == "codelist_urn"
    assert default[-1].edge.column == "codelist_urn"


def test_an_empty_path_resolves_to_nothing() -> None:
    assert resolve_path("dsd_component", ()) == ()


# ── errors name the alternatives ──────────────────────────────────────────


def test_an_unknown_hop_lists_what_is_reachable() -> None:
    """A caller who mistyped needs the alternatives more than the rule."""
    with pytest.raises(PathError, match=r"has no hop named 'conept'.*Reachable from here"):
        resolve_path("dsd_component", ("conept",))


def test_the_error_names_real_hops() -> None:
    with pytest.raises(PathError) as excinfo:
        resolve_path("dsd_component", ("nope",))
    message = str(excinfo.value)
    for expected in ("concept", "codelist", "dsd"):
        assert expected in message


def test_a_hop_valid_elsewhere_is_still_rejected_here() -> None:
    """`codes` is a real hop — from an artefact, not from a component."""
    with pytest.raises(PathError, match=r"has no hop named 'codes'"):
        resolve_path("dsd_component", ("codes",))


def test_the_failure_is_reported_at_the_hop_that_fails() -> None:
    with pytest.raises(PathError, match=r"'concept' has no hop named 'dsd'"):
        resolve_path("dsd_component", ("concept", "dsd"))


# ── planning ──────────────────────────────────────────────────────────────


def test_a_shared_prefix_produces_one_join() -> None:
    """The requirement the correlated-subquery benchmark turned up.

    Two paths through `concept` must join it once, not twice.
    """
    plan = plan_joins("dsd_component", [("concept",), ("concept", "codelist")])
    assert len(plan.joins) == 2
    assert plan.alias_of[("concept",)] == plan.alias_of[("concept",)]
    assert plan.to_sql().count("sdmx.concept") == 1


def test_repeating_a_path_adds_no_join() -> None:
    plan = plan_joins("dsd_component", [("dsd",), ("dsd",), ("dsd",)])
    assert len(plan.joins) == 1


def test_distinct_routes_to_one_table_get_distinct_joins() -> None:
    """`component.codelist` and `component.concept.codelist` are different
    questions and must not be collapsed into one join."""
    plan = plan_joins("dsd_component", [("codelist",), ("concept", "codelist")])
    assert plan.alias_of[("codelist",)] != plan.alias_of[("concept", "codelist")]
    assert plan.to_sql().count("sdmx.artefact") == 2


def test_aliases_are_stable_across_orderings() -> None:
    """Emitted SQL must not churn — a moving alias would diff the corpus."""
    first = plan_joins("dsd_component", [("dsd",), ("concept",)])
    second = plan_joins("dsd_component", [("concept",), ("dsd",)])
    assert first.to_sql() == second.to_sql()


def test_a_nullable_edge_becomes_a_left_join() -> None:
    """`dsd_component.concept_urn` is nullable, so projecting through it must
    not delete rows."""
    plan = plan_joins("dsd_component", [("concept",)])
    assert plan.joins[0].optional is True
    assert plan.joins[0].to_sql().startswith("LEFT JOIN")


def test_a_mandatory_edge_becomes_an_inner_join() -> None:
    plan = plan_joins("dsd_component", [("dsd",)])
    assert plan.joins[0].optional is False
    assert plan.joins[0].to_sql().startswith("JOIN")


def test_a_to_many_path_is_refused_with_the_reason() -> None:
    """Fan-out is a correlated subquery, not a join, and saying so is the
    difference between a clear refusal and a silently wrong row count."""
    with pytest.raises(PathError, match=r"crosses a to-many edge.*correlated subquery"):
        plan_joins("artefact", [("codes",)])


def test_a_to_many_hop_is_refused_even_mid_path() -> None:
    with pytest.raises(PathError, match=r"crosses a to-many edge"):
        plan_joins("artefact", [("components", "concept")])


# ── the plan is valid SQL ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("root", "paths"),
    [
        ("dsd_component", [("dsd",)]),
        ("dsd_component", [("concept",), ("concept", "codelist")]),
        ("dsd_component", [("codelist",), ("concept", "codelist"), ("dsd",)]),
        ("code", [("codelist",)]),
        ("concept", [("scheme",), ("codelist",)]),
        ("dataflow", [("dsd",)]),
    ],
    ids=["one-hop", "shared-prefix", "both-codelist-routes", "code", "concept", "dataflow"],
)
def test_the_emitted_from_clause_executes(root: str, paths: list[tuple[str, ...]]) -> None:
    """A join tree that deduplicates but names a column wrongly is worth
    nothing, and only the database can say."""
    plan = plan_joins(root, paths)
    _conn().execute(f"SELECT 1 FROM {plan.to_sql()} LIMIT 0").fetchall()


def test_every_alias_is_selectable() -> None:
    """Each joined relation must actually be reachable by its alias."""
    plan = plan_joins("dsd_component", [("concept", "codelist"), ("dsd",)])
    columns = ", ".join(f"{alias}.*" for alias in plan.alias_of.values())
    _conn().execute(f"SELECT {columns} FROM {plan.to_sql()} LIMIT 0").fetchall()


def test_hop_exposes_the_edge_it_walked() -> None:
    (hop,) = resolve_path("code", ("codelist",))
    assert isinstance(hop, Hop)
    assert hop.edge.source == "code"
    assert hop.edge.column == "codelist_urn"


# ── The suggestion list, which is the point of the error ─────────────────────


def test_the_reachable_list_is_the_hops_of_this_relation_only() -> None:
    """`_hops_available` decides what a mistyped hop is offered instead.

    Found by mutation (#304): five mutants of this function survived the whole
    of this file. Every one produced a *wrong* suggestion list — inverting
    `e.source == table` offers the hops of every relation except the one you
    are on — and nothing noticed, because the tests asserted that `PathError`
    was raised and never what it said.

    That is worth closing rather than waiving. `resolve_path`'s own docstring
    says the alternatives matter more than the rule ("a caller who mistyped a
    hop needs the alternatives more than the rule"), so the list *is* the
    feature here; a `PathError` naming the wrong hops is a worse failure than
    one naming none.

    Derived from `EDGES` rather than hand-listed, so a new edge on `code`
    appears here without anyone remembering to add it.
    """
    from sdmxlib.query._edges import EDGES
    from sdmxlib.query._joins import _hops_available

    got = set(_hops_available("code"))

    forward = {e.name for e in EDGES if e.source == "code" and e.name}
    backward = {e.inverse for e in EDGES if e.target == "code" and e.inverse}
    assert got == forward | backward, "the list is exactly this relation's hops, both directions"

    # Discriminating half: hops that belong to *another* relation must not be
    # offered. Without this the inverted-filter mutants all survive, because
    # a superset still contains everything the equality check looks for.
    elsewhere = {e.name for e in EDGES if e.source != "code" and e.name}
    assert not (got & (elsewhere - forward - backward)), (
        "suggestions leaked in from relations you cannot reach from here"
    )


def test_a_facet_is_offered_only_where_its_base_is_reachable() -> None:
    """The fifth survivor: `f.base in inverses` inverted to `not in`.

    A facet hop (`dimensions`, `attributes`, `measures`) is a narrowing of a
    base edge, so it is only reachable where that base is. Offering the others
    would send a caller down a path that cannot resolve.
    """
    from sdmxlib.query._edges import FACETS
    from sdmxlib.query._joins import _hops_available

    on_dsd = set(_hops_available("artefact"))
    on_code = set(_hops_available("code"))
    facet_names = {f.name for f in FACETS}

    assert facet_names & on_dsd, "the DSD's component facets are reachable from the spine"
    assert not (facet_names & on_code), "a code has no component facets"


def test_the_message_lists_what_is_reachable() -> None:
    """End to end: the list reaches the caller, sorted and readable."""
    from sdmxlib.query._joins import PathError, resolve_path

    with pytest.raises(PathError) as excinfo:
        resolve_path("code", ("nope",))

    message = str(excinfo.value)
    assert "'code' has no hop named 'nope'" in message
    for hop in ("ancestors", "annotations", "children", "codelist", "descendants", "parent"):
        assert hop in message
    assert message.index("ancestors") < message.index("parent"), "sorted, so the list is stable"


# ── the root's schema is a property of the plan, not a literal (#297) ──────


def test_the_root_carries_its_own_schema() -> None:
    """`sdmx.` was written into three renderings and read as formatting.

    It was an assumption: that everything this compiler can reach lives in one
    schema of ours. A dataflow's observations do not — a binding names them
    `<database_role>.<table_location>` — and #297's data-plane vocabulary
    needs this compiler over them.
    """
    plan = plan_joins("artefact", [()], root_alias="s0")
    assert plan.root_sql == "sdmx.artefact"
    assert plan.to_sql().startswith("sdmx.artefact s0")


def test_an_already_qualified_root_is_not_prefixed_again() -> None:
    """The data-plane case: a two-part name that must survive intact.

    `memory.data.obs` prefixed once more is `sdmx.memory.data.obs`, which
    parses and names nothing.
    """
    plan = plan_joins("memory.data.obs", [()], root_alias="s0", root_schema=None)
    assert plan.root_sql == "memory.data.obs"
    assert plan.to_sql() == "memory.data.obs s0"


def test_a_hop_is_structural_whatever_the_root_is() -> None:
    """Only the root can live elsewhere.

    Every declared edge targets a table in `EDGES`, and those are all `sdmx` —
    so a joined relation is qualified by the constant while the root is
    qualified by the field. A data-plane query has no hops at all, which is
    why this asymmetry costs nothing.
    """
    plan = plan_joins("code", [("codelist",)], root_alias="s0")
    joined = [line for line in plan.to_sql().splitlines() if line.startswith("JOIN")]
    assert joined, "the codelist hop should have produced a join"
    assert all(line.split()[1].startswith("sdmx.") for line in joined)


# ── the USING shape, for the data plane's sidecar (#297 step 2) ────────────


def test_a_using_join_names_its_columns() -> None:
    """The shape a series-attributes sidecar needs.

    `SchemaInspector.plan` already derives the columns common to a binding's
    main and sidecar tables; today the data plane renders that join as a
    string. Modelling it lets the same planner produce it.
    """
    join = Join("s1", "data.series_attrs", optional=True, using=("SERIES_KEY", "FREQ"), schema=None)
    assert join.to_sql() == "LEFT JOIN data.series_attrs s1 USING (SERIES_KEY, FREQ)"


def test_a_join_is_on_or_using_and_never_both() -> None:
    """The invariant, enforced rather than documented.

    Two mutually exclusive shapes on one class is the arrangement that lets a
    caller set both and get whichever `to_sql` happens to check first. It
    raises instead, and names the join so the message points somewhere.
    """
    with pytest.raises(ValueError, match="not both"):
        Join("s1", "code", optional=False, on="a = b", using=("k",))
    with pytest.raises(ValueError, match="not neither"):
        Join("s1", "code", optional=False)


def test_a_structural_hop_still_qualifies_itself() -> None:
    """The default is unchanged, which is what keeps the refactor invisible."""
    join = Join("s1", "codelist", optional=False, on="s1.urn = s0.codelist_urn")
    assert join.to_sql() == "JOIN sdmx.codelist s1 ON s1.urn = s0.codelist_urn"


# ── a root's identity is read from the instance (#297 step 3.5) ────────────


def test_a_root_may_carry_its_table_per_instance() -> None:
    """The compiler used to read `type(root).__table__`, fixing the table to
    the class.

    Every structural accessor is a singleton whose relation is a property of
    its type, so that read was correct and invisible. A dataflow's
    observations are not: the table comes from a *binding*, so two roots of
    the same class name two different tables. Reading the instance costs
    nothing for the singletons — a class attribute is visible on the instance
    — and is what lets #297 step 4 have a root at all.

    Asserted here rather than in the data plane because that is where the
    behaviour lives, and because it must keep holding after the data plane
    arrives.
    """
    from sdmxlib.query.accessors import q

    assert q.code.__table__ == "code", "the singleton still answers from its class"

    class _PerInstance:
        __table__ = "unset"
        __key__: tuple[str, ...] = ()
        __id__ = ""
        __constraint__: tuple[str, tuple[str, ...]] | None = None

        def __init__(self, table: str) -> None:
            self.__table__ = table

    one, two = _PerInstance("memory.data.obs"), _PerInstance("memory.data.other")
    assert one.__table__ != two.__table__, "two roots of one class, two tables"
    assert plan_joins(one.__table__, [()], root_schema=None).to_sql() == "memory.data.obs t0"
    assert plan_joins(two.__table__, [()], root_schema=None).to_sql() == "memory.data.other t0"
