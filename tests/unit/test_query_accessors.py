"""Accessors record paths, and every path they can build is a declared edge.

The second half is the point. A path is meant to be a machine-checked proof
that a route exists, and that only holds while every hop an accessor exposes
corresponds to an `Edge` in `sdmxlib.query._edges`. An accessor that grew an
attribute the edge graph does not know about would compile to a join the drift
guard never checks.
"""

from __future__ import annotations

import annotationlib

import duckdb
import pytest

import sdmxlib.query.accessors
from sdmxlib.query import EDGES, FACETS
from sdmxlib.query._expr import (
    AnyCol,
    AnyCols,
    BoolOp,
    Cols,
    Compare,
    FanOut,
    InSet,
    IsNull,
    Localised,
    Not,
    Num,
    Quantified,
    Str,
    col,
)
from sdmxlib.query.accessors import CodeRow, _Late, q
from sdmxlib.storage.schema import create_schema

pytestmark = pytest.mark.unit


# ── paths ─────────────────────────────────────────────────────────────────


def test_a_root_column_has_no_path() -> None:
    assert q.component.id.path == ()
    assert q.component.id.table == "dsd_component"
    assert q.component.id.column == "component_id"


def test_one_hop_records_the_edge_name() -> None:
    ref = q.component.dsd.agency
    assert ref.path == ("dsd",)
    assert ref.table == "artefact"
    assert ref.column == "agency_id"


def test_two_hops_record_both_in_order() -> None:
    assert q.component.concept.codelist.id.path == ("concept", "codelist")


def test_the_maintainable_surface_is_hoisted_not_pathed() -> None:
    """`dataflow.agency` has no hop: the identity split is layout, not a choice."""
    assert q.dataflow.agency.path == ()
    assert q.dataflow.agency.table == "artefact"


def test_paths_do_not_leak_between_accesses() -> None:
    """Accessors are shared singletons, so walking one must not mutate it."""
    deep = q.component.concept.codelist.id
    assert deep.path == ("concept", "codelist")
    assert q.component.id.path == ()
    assert q.component.dsd.agency.path == ("dsd",)


def test_the_two_routes_to_a_codelist_are_different_paths() -> None:
    """A component's own representation, and its concept's default.

    Both spellable, neither guessed — they agree until a structure overrides,
    which is why leaving it to a compiler to choose would be the worst kind of
    wrong.
    """
    own = q.component.codelist.id
    default = q.component.concept.codelist.id
    assert own.path == ("codelist",)
    assert default.path == ("concept", "codelist")
    assert own.path != default.path


# ── every exposed hop is a declared edge ──────────────────────────────────


def _transitions() -> dict[tuple[str, str], str]:
    """`(table, hop name) -> table reached`, over every declared traversal."""
    out: dict[tuple[str, str], str] = {}
    for e in EDGES:
        if e.name is not None:
            out[e.source, e.name] = e.target
        if e.inverse is not None:
            out[e.target, e.inverse] = e.source
            for f in FACETS:
                if f.base == e.inverse:
                    out[e.target, f.name] = e.source
    return out


def _accessor_edges() -> list[tuple[str, str, tuple[str, ...]]]:
    """(root name, its table, declared path) for every edge an accessor exposes.

    The whole path, not just its first hop. `q.dataflow.dsd` is the compound
    `("dataflow", "dsd")` — the identity split is crossed and then the FK — and
    a guard reading only `dsd` would validate a hop that does not start where
    it claims to.
    """
    found: list[tuple[str, str, tuple[str, ...]]] = []
    for name in annotationlib.get_annotations(type(q), format=annotationlib.Format.STRING):
        root = getattr(q, name)
        table = type(root).__table__
        for attr in dir(type(root)):
            if attr.startswith("_"):
                continue
            value = getattr(root, attr)
            if isinstance(value, Cols):
                found.append((name, table, value.path))
    return found


def test_accessors_expose_at_least_the_known_hops() -> None:
    hops = {(t, p[0]) for _, t, p in _accessor_edges() if p}
    for expected in (
        ("dsd_component", "dsd"),
        ("dsd_component", "concept"),
        ("dsd_component", "codelist"),
        ("concept", "scheme"),
        ("concept", "codelist"),
        ("code", "codelist"),
    ):
        assert expected in hops, f"accessor lost the {expected[0]}.{expected[1]} hop"


@pytest.mark.parametrize(
    ("root", "table", "path"),
    [case for case in _accessor_edges() if case[2]],
    # The root is in the id because several accessors reach the same
    # (table, path) — every artefact-rooted one exposes `annotations` — so
    # without it four cases share an id and a failure names none of them.
    ids=lambda v: v if isinstance(v, str) else ".".join(v),
)
def test_every_exposed_hop_is_a_declared_edge(root: str, table: str, path: tuple[str, ...]) -> None:
    """No accessor may expose a hop the edge graph does not declare.

    Otherwise the path stops being a proof: it would compile to a join that
    `test_query_edges.py` never validated against the schema. Walked hop by
    hop, so a compound path is checked at each table it passes through rather
    than only where it starts.
    """
    transitions = _transitions()
    at = table
    for hop in path:
        assert (at, hop) in transitions, f"q.{root} exposes {at}.{hop}, which is not in EDGES or FACETS"
        at = transitions[at, hop]


# ── predicates ────────────────────────────────────────────────────────────


def test_comparison_builds_a_predicate_not_a_bool() -> None:
    pred = q.component.dsd.agency == "ESTAT"
    assert isinstance(pred, Compare)
    assert pred.op == "="
    assert pred.value == "ESTAT"
    assert pred.column.path == ("dsd",)


def test_inequality_builds_a_predicate() -> None:
    assert isinstance(q.component.id != "FREQ", Compare)


def test_numeric_columns_carry_ordering() -> None:
    assert isinstance(q.component.position > 1, Compare)
    assert isinstance(q.code.depth <= 3, Compare)
    assert isinstance(q.component.position, Num)
    assert isinstance(q.component.id, Str)


def test_null_checks() -> None:
    assert isinstance(q.component.codelist.id.is_null(), IsNull)
    not_null = q.component.codelist.id.is_not_null()
    assert isinstance(not_null, IsNull)
    assert not_null.negate is True


def test_predicates_compose() -> None:
    left = q.component.id == "FREQ"
    # `"Dimension"`, capitalised: `role` is a closed vocabulary and the
    # lower-case spelling this test used to carry matched nothing.
    right = q.component.role == "Dimension"
    conjunction = left & right
    disjunction = left | right
    negation = ~left
    assert isinstance(conjunction, BoolOp)
    assert isinstance(disjunction, BoolOp)
    assert isinstance(negation, Not)
    assert conjunction.op == "and"
    assert disjunction.op == "or"
    assert negation.part is left


def test_is_in_takes_an_iterable() -> None:
    pred = q.component.id.is_in(["FREQ", "REF_AREA"])
    assert isinstance(pred, InSet)
    assert pred.values == ("FREQ", "REF_AREA")


def test_is_in_refuses_a_bare_string() -> None:
    """`is_in("CA")` meaning `is_in(["C", "A"])` shipped once, in #261.

    The message names the fix, which is how this library handles a call that is
    well-typed and wrong.
    """
    with pytest.raises(TypeError, match=r"is_in\(\['C', 'A'\]\).*Pass a list"):
        q.component.id.is_in("CA")


# ── the namespace ─────────────────────────────────────────────────────────


def test_every_root_is_reachable() -> None:
    for name in ("artefact", "dataflow", "dsd", "codelist", "concept_scheme", "component", "code", "concept"):
        root = getattr(q, name)
        assert isinstance(root, Cols)
        assert root.path == ()


def test_a_component_has_no_agency_of_its_own() -> None:
    """It is not maintainable, so reaching an agency means saying whose."""
    assert not hasattr(q.component, "agency")
    assert q.component.dsd.agency.path == ("dsd",)


# ── fan-out ───────────────────────────────────────────────────────────────


def test_dotting_through_a_fan_out_keeps_the_parent_root() -> None:
    """`dsd.components.concept.id` is about a DSD row, reached through joins."""
    ref = q.dsd.components.concept.id
    assert ref.path == ("components", "concept")
    assert ref.table == "concept"


def test_a_fan_out_is_the_child_accessor() -> None:
    """Which is why dotting through it works at all."""
    assert isinstance(q.dsd.components, Cols)
    assert q.dsd.components.id.column == "component_id"
    assert q.codelist.codes.id.column == "code_id"


def test_fan_out_is_marked_in_the_type() -> None:
    """Cardinality change is visible in the type, not in a naming convention."""
    assert isinstance(q.dsd.components, FanOut)
    assert isinstance(q.codelist.codes, FanOut)
    assert not isinstance(q.component.dsd, FanOut)
    assert not isinstance(q.component.concept, FanOut)


def test_any_groups_predicates_onto_one_child() -> None:
    """The distinction dot-through alone cannot express."""
    pred = q.dsd.components.any(
        q.component.concept.id == "REF_AREA",
        q.component.codelist.id == "CL_GEO",
    )
    assert isinstance(pred, Quantified)
    assert pred.quantifier == "any"
    assert pred.path == ("components",)
    assert len(pred.parts) == 2


def test_every_is_the_universal() -> None:
    pred = q.dsd.components.every(q.component.codelist.id == "CL_GEO")
    assert isinstance(pred, Quantified)
    assert pred.quantifier == "every"


def test_independent_and_same_child_are_different_predicates() -> None:
    """Two dot-through predicates versus one `.any()` with two — verified in
    `query-language-evidence.md` to return different rows."""
    independent = (q.dsd.components.concept.id == "REF_AREA") & (q.dsd.components.codelist.id == "CL_GEO")
    same_child = q.dsd.components.any(
        q.component.concept.id == "REF_AREA",
        q.component.codelist.id == "CL_GEO",
    )
    assert not isinstance(independent, Quantified)
    assert isinstance(same_child, Quantified)


def test_flows_is_a_fan_out_not_a_path() -> None:
    """A DSD backs many dataflows — the edge that looked to-one in seven
    design examples until real rows were run."""
    assert isinstance(q.dsd.dataflows, FanOut)


def test_a_codelist_has_no_components() -> None:
    """The accessor is more precise than the storage edge, which points at
    `artefact` because that is where DSDs live."""
    assert not hasattr(q.codelist, "components")
    assert hasattr(q.dsd, "components")


# ── facets: pre-filtered fan-outs ─────────────────────────────────────────


def test_facets_are_fan_outs_with_their_own_path() -> None:
    """`dimensions` is `components` plus a constant, so it fans out too."""
    assert isinstance(q.dsd.dimensions, FanOut)
    assert q.dsd.dimensions.concept.id.path == ("dimensions", "concept")
    assert q.dsd.attributes.id.path == ("attributes",)
    assert q.dsd.measures.id.path == ("measures",)


def test_facets_and_the_unfiltered_edge_are_different_paths() -> None:
    assert q.dsd.components.id.path == ("components",)
    assert q.dsd.dimensions.id.path == ("dimensions",)


def test_facets_take_the_same_binder_as_the_edge() -> None:
    pred = q.dsd.dimensions.any(
        q.component.concept.id == "REF_AREA",
        q.component.codelist.id == "CL_GEO",
    )
    assert isinstance(pred, Quantified)
    assert pred.path == ("dimensions",)
    assert len(pred.parts) == 2


def test_facet_values_match_what_the_writer_stores() -> None:
    """`component_type` holds SDMX class names, not lowercased roles.

    A facet declaring `"dimension"` would match nothing and fail silently — an
    empty result is not an error.
    """
    by_name = {f.name: f for f in FACETS}
    assert by_name["dimensions"].values == ("Dimension",)
    assert by_name["attributes"].values == ("Attribute",)
    assert by_name["measures"].values == ("Measure",)


def test_dimensions_excludes_the_time_dimension() -> None:
    """Matching `DataStructure.dimensions`, where the time dimension is a
    separate scalar field rather than a member."""
    by_name = {f.name: f for f in FACETS}
    assert "TimeDimension" not in by_name["dimensions"].values
    assert "MeasureDimension" not in by_name["dimensions"].values


def test_every_facet_refines_a_declared_inverse() -> None:
    inverses = {e.inverse for e in EDGES if e.inverse}
    for facet in FACETS:
        assert facet.base in inverses, f"{facet.name} refines an unknown edge"


# ── every declared column exists ──────────────────────────────────────────


def _declared_columns() -> list[tuple[str, str]]:
    """(table, column) for every column any accessor can produce."""
    found: set[tuple[str, str]] = set()

    def walk(node: object, depth: int) -> None:
        if depth > 3:
            return
        for attr in dir(type(node)):
            if attr.startswith("_"):
                continue
            value = getattr(node, attr)
            if isinstance(value, AnyCol):
                # `physical` where it exists: a `Label`'s `column` carries the
                # language, because that is the *output* name — `name_en`, so
                # two languages can share a frame — and the stored column is
                # still `name`. This guard is about the schema, so it wants
                # the stored one.
                found.add((value.table, getattr(value, "physical", value.column)))
            elif isinstance(value, Localised):
                label = value["en"]
                found.add((label.table, label.physical))
            elif isinstance(value, Cols):
                walk(value, depth + 1)

    for name in ("artefact", "dataflow", "dsd", "codelist", "concept_scheme", "component", "code", "concept"):
        walk(getattr(q, name), 0)
    return sorted(found)


@pytest.mark.parametrize(("table", "column"), _declared_columns())
def test_every_declared_column_exists(table: str, column: str) -> None:
    """An accessor naming a column the schema lacks fails at *query* time, in
    whatever query first touches it, with a DuckDB error rather than ours.

    Caught one immediately: a maintainable's labels live in
    `sdmx.localized_text` keyed by (owner_urn, field, lang), not in an inline
    `name` MAP like `code.name` — so `q.dsd.name` was declared against a column
    that does not exist.
    """
    conn = duckdb.connect()
    create_schema(conn)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'sdmx' AND table_name = ?",
            [table],
        ).fetchall()
    }
    assert names, f"no such table: {table}"
    assert column in names, f"{table} has no column {column}"


# ── reserved names ────────────────────────────────────────────────────────


def test_no_accessor_shadows_a_reserved_member() -> None:
    """A column named `path` makes `AnyCol.__get__` call itself, for ever.

    `__get__` binds a declaration onto the reading accessor by asking it for
    `obj.path`. Declare a column of that name and the lookup finds the column
    descriptor instead of `AnyCols.path`, whose `__get__` asks for `obj.path`
    again. The failure is a `RecursionError` at first use, nowhere near the
    declaration — which is how `q.reported_attribute` was written with a `path`
    column and only found when something read it.

    Checked against the base's own members rather than a hand-listed set, so a
    new one on `AnyCols` is covered without touching this.
    """
    reserved = {name for name in vars(AnyCols) if not name.startswith("__")}
    assert "path" in reserved, "the case this guard exists for"
    for root_name in annotationlib.get_annotations(type(q), format=annotationlib.Format.STRING):
        accessor = type(getattr(q, root_name))
        # Everything the accessor tree declares *below* `AnyCols` — the base's
        # own members are what is being protected, not a violation of it.
        subclasses = accessor.__mro__[: accessor.__mro__.index(AnyCols)]
        declared = {name for klass in subclasses for name in vars(klass) if not name.startswith("_")}
        clash = declared & reserved
        assert not clash, f"q.{root_name} declares {sorted(clash)}, which AnyCols already uses"


def test_binding_a_declaration_survives_a_shadowed_path() -> None:
    """The guard above forbids the shadowing; this shows why it has to.

    `__get__` binds a declaration onto the reading accessor by reading its
    path. Through the `path` *property* that is re-entrant the moment an
    accessor declares a column of that name — a `RecursionError` a thousand
    frames from the declaration. Reading `_path` instead makes the shadowing
    merely wrong rather than fatal, which is what lets the guard report it.

    Built here rather than found in `sl.q`, because `sl.q` is not allowed to
    contain one.
    """

    class _Shadowing(Cols[CodeRow]):
        __slots__ = ()
        __table__ = "code"

        # Shadowing `AnyCols.path` is the subject of this test, so the
        # incompatible override is deliberate rather than a defect.
        path: Str[CodeRow] = col("code", "code_id")  # pyrefly: ignore[bad-override, missing-override-decorator]

    shadowed = _Shadowing(("codes",))
    assert shadowed.path.column == "code_id"
    assert shadowed.path.path == ("codes",), "the declaration still binds the accessor's route"


# ── late-bound tree relationships ─────────────────────────────────────────


def test_every_edge_declaration_binds_where_it_says() -> None:
    """Every edge an accessor declares binds an accessor on a path naming it.

    Two shapes are declared lazily, as `_Late`: a tree accessor's
    `ancestors` is a `_ManyCode`, which is a `_Code` still being defined, and
    `_Dataflow.constraints` reaches a class that reaches back to `_Dataflow`.
    They were once an annotation in the body bound later by `setattr`, and the
    two could disagree: `_Concept` acquired three fan-out annotations by copy
    from `_Code` with no binding at all, which type-checked and raised
    `AttributeError` at runtime. A `_Late` *is* its binding, so that gap is
    closed; what is left to check is the path each one lands on.

    Over **every** member of every accessor rather than a hand-listed set of
    names: an earlier version named `code`, `category` and `node`, and so did
    not see `_Concept`. A guard that enumerates what it guards cannot catch the
    case nobody thought to add.
    """
    accessors = [
        klass
        for klass in vars(sdmxlib.query.accessors).values()
        if isinstance(klass, type) and issubclass(klass, Cols) and klass is not Cols
    ]
    checked = 0
    late = 0
    for klass in accessors:
        annotated = {
            name: annotation.strip('"').split("[")[0]
            for name, annotation in annotationlib.get_annotations(klass, format=annotationlib.Format.STRING).items()
            if not name.startswith("__") and annotation.strip('"').startswith("_")
        }
        lazy = {name for name, member in vars(klass).items() if isinstance(member, _Late)}
        late += len(lazy)
        for name in annotated.keys() | lazy:
            checked += 1
            bound = getattr(klass, name, None)
            assert isinstance(bound, Cols), f"{klass.__name__}.{name} is declared but binds no accessor"
            if name in annotated:
                assert type(bound).__name__ == annotated[name], (
                    f"{klass.__name__}.{name} is annotated {annotated[name]} but bound {type(bound).__name__}"
                )
            if not bound.path:
                # A **zero-hop namespace** — `component.facet`, whose columns
                # live on the owner's own row. It binds the empty path
                # deliberately, so the edge assertions below do not apply.
                # What must hold instead is that it reads the *same table*:
                # a namespace declared over another relation would resolve its
                # columns against a table the query never joined, which is
                # invalid SQL at best and silently the wrong row at worst.
                assert bound.__table__ == klass.__table__, (
                    f"{klass.__name__}.{name} is a zero-hop namespace over "
                    f"{bound.__table__!r}, but its owner is {klass.__table__!r}. "
                    f"A namespace reads the owner's row; crossing a relation "
                    f"needs an edge."
                )
                continue
            # A compound hop crosses the identity split, so its path carries
            # `spine` or the extension name as well — `_Dsd.dataflows` is
            # ("dataflows", "spine"). The attribute name must still be *in* it.
            assert name in bound.path, f"{klass.__name__}.{name} carries the path {bound.path}"
            if len(bound.path) == 1:
                assert bound.path == (name,), f"{klass.__name__}.{name} carries the path {bound.path}"
    # Exact: four tree relationships on each of five tree accessors, two on the
    # organisation unit (`parent`, `children`: no baked ancestry, #469), and
    # the 17 edges whose target reaches back. A change moves this deliberately.
    assert late == 5 * 4 + 2 + 17, f"{late} lazy declarations found"
    assert checked > late, "the eager declarations were not found beside the lazy ones"


def test_a_tree_accessor_declares_the_relationships_its_bake_supports() -> None:
    """`ancestors` and `descendants` come as a pair, and only where the writer
    bakes the closure.

    Ancestry here is a *stored* fact, never a recursive query, so a relation
    without `depth` / `ancestor_ids` genuinely has no transitive relationships
    to expose — `agency` is that case, and carries `parent` / `children`
    alone. A partial set anywhere else means an annotation was copied without
    its edges.
    """
    tree_names = {"ancestors", "descendants", "children", "parent"}
    for klass in vars(sdmxlib.query.accessors).values():
        if not (isinstance(klass, type) and issubclass(klass, Cols)):
            continue
        declared = {
            name
            for base in klass.__mro__
            for name in (*annotationlib.get_annotations(base, format=annotationlib.Format.STRING), *vars(base))
            if name in tree_names
        }
        if not declared:
            continue
        assert {"parent", "children"} <= declared, (
            f"{klass.__name__} declares {sorted(declared)} without parent/children"
        )
        transitive = declared & {"ancestors", "descendants"}
        assert transitive in ({"ancestors", "descendants"}, set()), (
            f"{klass.__name__} declares {sorted(transitive)} — the closure is one edge read both ways"
        )


def test_a_tree_relationship_lands_on_its_own_relation() -> None:
    """`ancestors` walks `code -> code`, not `code -> anything`. Cheap to get
    wrong in a late binding, and invisible until a query runs."""
    for accessor in (q.code, q.category, q.hcode):
        table = type(accessor).__table__
        for name in ("ancestors", "descendants", "children", "parent"):
            assert type(getattr(accessor, name)).__table__ == table, name
