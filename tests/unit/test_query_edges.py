"""The edge graph must agree with the schema it claims to describe.

A mis-declared edge fails **silently** — it multiplies or drops rows — where a
missing column fails loudly. `docs/design/query-language-examples.md` §5 has the
case that motivated this: `component.flows` was written as a to-one path in
seven examples, and only running three rows through DuckDB showed it duplicating
one component and dropping another.

So cardinality is never declared. It is derived from key constraints here, and
these tests are what make that derivation trustworthy.
"""

from __future__ import annotations

import functools
import pathlib

import duckdb
import pytest

from sdmxlib.query import EDGES, FACETS, Edge, Facet, edges_from, edges_into
from sdmxlib.storage.schema import create_schema

pytestmark = pytest.mark.unit


@functools.cache
def _schema() -> dict[str, dict[str, bool]]:
    """Every sdmx table, mapping column name to whether it is NOT NULL."""
    conn = duckdb.connect()
    create_schema(conn)
    rows = conn.execute(
        """
        SELECT table_name, column_name, is_nullable
        FROM information_schema.columns WHERE table_schema = 'sdmx'
        """
    ).fetchall()
    out: dict[str, dict[str, bool]] = {}
    for table, column, nullable in rows:
        out.setdefault(str(table), {})[str(column)] = str(nullable).upper() == "NO"
    return out


@functools.cache
def _column_types() -> dict[tuple[str, str], str]:
    """``(table, column)`` → DuckDB's declared type, e.g. ``VARCHAR[]``."""
    conn = duckdb.connect()
    create_schema(conn)
    rows = conn.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'sdmx'"
    ).fetchall()
    return {(str(t), str(c)): str(d) for t, c, d in rows}


def _column_type(table: str, column: str) -> str:
    return _column_types()[(table, column)]


@functools.cache
def _reference_shaped_columns() -> frozenset[tuple[str, str]]:
    """`(table, column)` for every `*_urn` / `*_id` column worth a decision.

    Scoped to the relations the edge graph already knows — a table with no
    edges at all is a coverage question, not an un-wired reference, and folding
    the two together would bury this signal under twenty rows.

    Primary-key columns are excluded: those are the row's own identity, not an
    outgoing reference. Composite keys leave their parts visible, which is why
    `code.code_id` and friends still need an entry in `NOT_EDGES`.
    """
    known = {edge.source for edge in EDGES} | {edge.target for edge in EDGES}
    conn = duckdb.connect()
    create_schema(conn)
    keys: dict[str, set[str]] = {}
    for table, columns in conn.execute(
        """
        SELECT table_name, constraint_column_names FROM duckdb_constraints()
        WHERE schema_name = 'sdmx' AND constraint_type = 'PRIMARY KEY'
        """
    ).fetchall():
        keys.setdefault(str(table), set()).update(str(c) for c in columns)
    return frozenset(
        (str(table), str(column))
        for table, column in conn.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'sdmx'
            """
        ).fetchall()
        if str(table) in known
        and (str(column).endswith(("_urn", "_id")))
        and str(column) not in keys.get(str(table), set())
    )


@functools.cache
def _unique_columns() -> dict[str, frozenset[str]]:
    """Single-column uniqueness per table, from primary keys and unique indexes.

    Only *single-column* uniqueness counts: a composite key does not make
    either of its columns unique, which is exactly the case that decides
    whether an edge's reverse direction is to-one or to-many.
    """
    conn = duckdb.connect()
    create_schema(conn)
    out: dict[str, set[str]] = {}
    constraints = conn.execute(
        """
        SELECT table_name, constraint_column_names
        FROM duckdb_constraints()
        WHERE schema_name = 'sdmx' AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        """
    ).fetchall()
    for table, columns in constraints:
        cols = list(columns)
        if len(cols) == 1:
            out.setdefault(str(table), set()).add(str(cols[0]))
    for table, sql in conn.execute(
        "SELECT table_name, sql FROM duckdb_indexes() WHERE schema_name = 'sdmx' AND is_unique"
    ).fetchall():
        inner = str(sql).rsplit("(", 1)[-1].rstrip(");").strip()
        if "," not in inner:
            out.setdefault(str(table), set()).add(inner.strip('"'))
    return {t: frozenset(c) for t, c in out.items()}


def _is_unique(table: str, columns: tuple[str, ...]) -> bool:
    """Whether the schema declares this exact column tuple unique.

    A *tuple*, since composite references are the rule for the tree relations:
    `code.code_id` is not unique but `(codelist_urn, code_id)` is, and reading
    only the second column would call a self-reference to-many when it is not.
    """
    if len(columns) == 1:
        return columns[0] in _unique_columns().get(table, frozenset())
    return columns in _declared_key_tuples().get(table, frozenset())


@functools.cache
def _declared_key_tuples() -> dict[str, frozenset[tuple[str, ...]]]:
    """Every declared multi-column key, per table."""
    conn = duckdb.connect()
    create_schema(conn)
    out: dict[str, set[tuple[str, ...]]] = {}
    for table, columns in conn.execute(
        """
        SELECT table_name, constraint_column_names
        FROM duckdb_constraints()
        WHERE schema_name = 'sdmx' AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        """
    ).fetchall():
        out.setdefault(str(table), set()).add(tuple(str(c) for c in columns))
    return {t: frozenset(k) for t, k in out.items()}


def _forward_is_to_one(edge: Edge) -> bool:
    """Source to target is to-one iff the target tuple is unique **and** the
    match is by equality.

    Membership is the exception the key constraints cannot see: one row's list
    matches many target rows however unique the target key is. Reading it off
    the declaration rather than the schema is the one thing this guard takes on
    trust, and `test_a_membership_edge_is_to_many_in_both_directions` is what
    makes that safe.
    """
    if edge.member_of is not None:
        return False
    return _is_unique(edge.target, tuple(t for _, t in edge.columns))


def _backward_is_to_one(edge: Edge) -> bool:
    return edge.member_of is None and _is_unique(edge.source, tuple(s for s, _ in edge.columns))


# ── the declarations describe real columns ────────────────────────────────


#: `name` is `None` on the four annotation edges — they differ only by
#: target — so the target has to be in the id or a failure names an edge
#: nobody can identify and `-k` cannot select one.
def _edge_id(edge: Edge) -> str:
    return f"{edge.source}.{edge.name}" if edge.name else f"{edge.source}->{edge.target}"


@pytest.mark.parametrize("edge", EDGES, ids=_edge_id)
def test_edge_endpoints_exist(edge: Edge) -> None:
    schema = _schema()
    assert edge.source in schema, f"no such table: {edge.source}"
    assert edge.target in schema, f"no such table: {edge.target}"
    for src, tgt in edge.columns:
        assert src in schema[edge.source], f"{edge.source} has no {src}"
        assert tgt in schema[edge.target], f"{edge.target} has no {tgt}"


def _is_identity_split(edge: Edge) -> bool:
    """Both endpoints are their own table's single-column key.

    A 1:1 join between two tables that share a key. Derived rather than named,
    so a new extension table is covered without touching this file.
    """
    return _forward_is_to_one(edge) and _backward_is_to_one(edge)


def _is_polymorphic(edge: Edge) -> bool:
    """A *forward-traversable* edge whose column also targets other relations.

    `organisation_contact.owner_urn` is the case: a contact belongs to an
    `Agency` or a `DataProvider`, so two named edges share one source column
    and point at two tables. Derived from `EDGES`, so a third owner is covered
    without touching this file.

    **Named only**, and that qualifier is load-bearing. `annotation.owner_urn`
    is polymorphic four ways — artefact, code, concept, hierarchy_node — and
    every one of those edges is `name=None`: reverse-only, so there is no
    `q.annotation.code` to traverse and no forward join whose kind could be
    wrong. Optionality governs the direction that exists; for a fan-out read
    backwards the compiler emits `EXISTS`, which has no LEFT/INNER to get
    wrong. Without this qualifier the rule fires on all four and demands a
    change that would mean nothing.
    """
    if edge.name is None:
        return False
    mine = tuple(src for src, _ in edge.columns)
    return (
        sum(
            1
            for other in EDGES
            if other.name is not None and other.source == edge.source and tuple(s for s, _ in other.columns) == mine
        )
        > 1
    )


@pytest.mark.parametrize("edge", EDGES, ids=_edge_id)
def test_optionality_matches_the_schema(edge: Edge) -> None:
    """`optional` drives the join kind, so it must not be guessed.

    Nullability decides it for a foreign key: a nullable FK declared
    `optional=False` becomes an INNER join and silently drops rows.

    Two cases nullability cannot decide, and both are "the *row* may be
    missing, not the column":

    - **An identity split out of the spine.** `artefact.urn` is NOT NULL, but
      the matching extension row exists only for artefacts of that type.
    - **A polymorphic owner.** `organisation_contact.owner_urn` is NOT NULL —
      every contact has an owner — but it points into `agency` *or*
      `data_provider`, so either join may find nothing. Declared
      `optional=False` these become INNER joins: each hop silently drops every
      contact of the other kind, and selecting both at once returns nothing at
      all. Measured, before they were marked optional (#309).

    Nothing in the schema records either (it declares no foreign keys), so both
    are derived here.
    """
    not_null = all(_schema()[edge.source][src] for src, _ in edge.columns)
    if _is_polymorphic(edge):
        mine = tuple(src for src, _ in edge.columns)
        siblings = sorted(
            {
                other.target
                for other in EDGES
                if other.name is not None and other.source == edge.source and tuple(s for s, _ in other.columns) == mine
            }
            - {edge.target}
        )
        assert edge.optional, (
            f"{edge.source}.{edge.column} is polymorphic — it also targets "
            f"{siblings}"
            f" — so this join may find nothing and must be optional"
        )
        return
    if _is_identity_split(edge):
        outward = edge.source == "artefact"
        assert edge.optional == outward, (
            f"{edge.source} -> {edge.target} is an identity split; "
            f"out of the spine is optional and into it is not, "
            f"but it declares optional={edge.optional}"
        )
        return
    assert edge.optional == (not not_null), (
        f"{edge.source}.{edge.column} is "
        f"{'NOT NULL' if not_null else 'nullable'}, "
        f"but the edge declares optional={edge.optional}"
    )


# ── cardinality, derived rather than declared ─────────────────────────────


@pytest.mark.parametrize("edge", [e for e in EDGES if e.member_of is None], ids=_edge_id)
def test_forward_direction_is_to_one(edge: Edge) -> None:
    """Dotting through `edge.name` must not multiply rows.

    An edge whose target column is not unique is a to-many hop wearing a
    to-one spelling, which is the defect this whole guard exists for.

    Membership edges are exempt and checked separately: they are to-many
    forwards *by construction*, which is the one thing the key constraints
    cannot tell you.
    """
    assert _forward_is_to_one(edge), (
        f"{edge.source}.{edge.name} points at {edge.target}.{edge.target_column}, "
        f"which is not unique — the forward direction would multiply rows"
    )


@pytest.mark.parametrize("edge", [e for e in EDGES if e.inverse], ids=lambda e: f"{e.target}.{e.inverse}")
def test_reverse_direction_is_to_many(edge: Edge) -> None:
    """An exposed inverse is a fan-out, and must be typed as one.

    If the source column were unique the reverse would be to-one, and exposing
    it as a plural attribute would be a lie.
    """
    assert not _backward_is_to_one(edge), (
        f"{edge.target}.{edge.inverse} is declared as a fan-out, but "
        f"{edge.source}.{edge.column} is unique — it is to-one"
    )


@pytest.mark.parametrize("edge", [e for e in EDGES if e.member_of is not None], ids=_edge_id)
def test_a_membership_edge_is_to_many_in_both_directions(edge: Edge) -> None:
    """The one cardinality fact read off the declaration rather than the schema.

    `(codelist_urn, code_id)` *is* unique, so the derivation would call
    `code -> ancestors` to-one — and it is not: a code has many ancestors, and
    an ancestor many descendants. Membership is what makes it so, and nothing
    in the constraints records it.

    So this asserts the two halves that keep the exemption honest: the target
    tuple really is unique (otherwise the exemption would be hiding an
    ordinary mis-declaration), and both directions really are to-many.
    """
    assert _is_unique(edge.target, tuple(t for _, t in edge.columns)), (
        f"{edge.source}.{edge.name} is exempt from the to-one guard because it "
        f"matches by membership — but its target tuple is not unique either, "
        f"which means the exemption is masking a mis-declared edge"
    )
    assert not _forward_is_to_one(edge)
    assert not _backward_is_to_one(edge)
    source_columns = tuple(s for s, _ in edge.columns)
    assert edge.member_of is not None, "a membership edge names the column it matches on"
    member = source_columns[edge.member_of]
    assert _column_type(edge.source, member).endswith("[]"), (
        f"{edge.source}.{member} is matched by membership but is not a list column"
    )
    if edge.member_ids is not None:
        # The parallel list of the members' own ids (#439): a list on the
        # source, an existing column on the target.
        id_list, id_column = edge.member_ids
        assert _column_type(edge.source, id_list).endswith("[]"), f"{edge.source}.{id_list} is not a list column"
        assert id_column in _schema()[edge.target], f"{edge.target} has no column {id_column}"


def test_the_identity_split_is_one_to_one() -> None:
    """Hoisting the maintainable surface is sound only while this holds.

    `sdmx.dataflow` is `urn PRIMARY KEY` against `sdmx.artefact`'s own `urn`:
    PK-to-PK, so it can neither multiply nor drop rows in the inward
    direction. If a schema change ever makes either side non-unique, hoisted
    columns would silently do both — so this fails loudly instead.
    """
    splits = [e for e in EDGES if e.name == "spine"]
    # Derived, not listed. An earlier version named the five extension tables
    # that existed when it was written, so every coverage slice adding a root
    # broke it for no reason — and a hand-listed set cannot catch the case
    # nobody thought to add, which is the failure this guard is for.
    #
    # What makes a table an extension is that `urn` alone is its primary key.
    assert len(splits) >= 5, "the identity split is the layout every maintainable root depends on"
    for edge in splits:
        assert _declared_key_tuples().get(edge.source, frozenset()) >= {("urn",)}, (
            f"{edge.source} carries a spine edge but `urn` is not its primary key, so it is not an extension table"
        )
    for edge in splits:
        assert _forward_is_to_one(edge), f"{edge.source} lost its 1:1 with artefact"
        assert _backward_is_to_one(edge), f"{edge.source}.{edge.column} is not unique"
        assert not edge.optional, f"{edge.source} -> artefact became optional"


# ── the graph is coherent as a namespace ──────────────────────────────────


def test_no_duplicate_attribute_on_one_accessor() -> None:
    """Two edges cannot claim the same attribute name on the same relation."""
    seen: dict[tuple[str, str], Edge] = {}
    for edge in EDGES:
        if edge.name is None:  # forward direction deliberately not exposed
            continue
        if _is_identity_split(edge):  # one per extension table, by construction
            continue
        key = (edge.source, edge.name)
        assert key not in seen, f"{edge.source}.{edge.name} declared twice"
        seen[key] = edge


def test_no_duplicate_inverse_on_one_accessor() -> None:
    seen: dict[tuple[str, str], Edge] = {}
    for edge in EDGES:
        if edge.inverse is None:
            continue
        key = (edge.target, edge.inverse)
        assert key not in seen, (
            f"{edge.target}.{edge.inverse} claimed by both "
            f"{seen[key].source}.{seen[key].column} and {edge.source}.{edge.column}"
        )
        seen[key] = edge


def test_lookup_helpers_agree_with_the_graph() -> None:
    assert {e.name for e in edges_from("dsd_component")} == {"dsd", "concept", "codelist"}
    assert "components" in {e.inverse for e in edges_into("artefact")}
    assert all(e.inverse is not None for e in edges_into("artefact"))


def test_the_two_routes_to_a_codelist_are_both_declared() -> None:
    """The pair that makes `dsd -> codelist` ambiguous, and must stay spellable.

    A component's own representation and its concept's default are different
    codelists whenever a structure overrides — they agree until someone does,
    which is the worst failure profile a silent choice can have.
    """
    own = [e for e in edges_from("dsd_component") if e.name == "codelist"]
    default = [e for e in edges_from("concept") if e.name == "codelist"]
    assert own
    assert default
    assert own[0].column == "codelist_urn"
    assert default[0].column == "codelist_urn"


# ── facets ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("facet", FACETS, ids=lambda f: f.name)
def test_facet_column_exists_on_the_child(facet: Facet) -> None:
    """A facet filters the child of the edge it refines, so the discriminator
    must be a column of that child rather than of the parent."""
    edge = next(e for e in EDGES if e.inverse == facet.base)
    assert facet.column in _schema()[edge.source], (
        f"{facet.name} filters on {edge.source}.{facet.column}, which does not exist"
    )


@pytest.mark.parametrize("facet", FACETS, ids=lambda f: f.name)
def test_facet_has_values(facet: Facet) -> None:
    """An empty value set matches nothing and fails silently."""
    assert facet.values


def test_facets_do_not_collide_with_edge_names() -> None:
    """A facet is exposed alongside inverses **on the same accessor**.

    Scoped per relation, because that is how `_resolve_one` reads them: it
    matches an inverse by `(target, name)` and a facet by `(target, base)`, so
    two relations may use one name without either shadowing the other.

    `dimensions` is the live case and it is deliberate — `dsd.dimensions` are
    the DSD's dimension components, `dsd.groups.dimensions` are the ones in a
    group, and the word means the same thing in both. A *global* ban would
    have forced a worse name on the second for a collision that cannot happen.
    What remains banned is the real hazard: one relation exposing a facet and
    an inverse under one name, where resolution order silently picks a winner.
    """
    by_table: dict[str, set[str]] = {}
    for edge in EDGES:
        if edge.inverse:
            by_table.setdefault(edge.target, set()).add(edge.inverse)
    for facet in FACETS:
        for edge in EDGES:
            if edge.inverse != facet.base:
                continue
            assert facet.name not in by_table.get(edge.target, set()), (
                f"{edge.target}.{facet.name} is both a facet and a declared inverse"
            )


#: Reference-shaped columns (`*_urn` / `*_id`) that are deliberately **not**
#: edges, each with the reason. The inventory below fails on anything absent
#: from both this set and `EDGES`, so a new one arrives as a decision rather
#: than as a column nobody wired.
#:
#: This exists because `hierarchy_node.level_id` sat here unnoticed: a level
#: could be named in a filter but its label and index were unreachable, and
#: nothing said whether that was a decision or an oversight. It was an
#: oversight. `concept.parent_id` was here too, described as dead — the
#: writer stored a constant NULL and the model had no parent field. That
#: was a defect rather than a decision, fixed in #307, and the entry is
#: gone because the edge exists now.
NOT_EDGES: dict[tuple[str, str], str] = {
    ("annotation_url", "link_urn"): (
        "a pointer, not a graph edge: SDMX-JSON's urn on the link spelling an annotation URL, held as text "
        "as `Link.urn` is, and naming anything SDMX — an artefact, an item, a report — or something never loaded (#422)"
    ),
    ("dsd_group", "attachment_constraint_urn"): (
        "always dangling: it names an SDMX-ML 2.1 AttachmentConstraint, which the store refuses (#395), "
        "so no `sdmx.artefact` row answers it (#422)"
    ),
    ("artefact", "agency_id"): (
        "under-determined, and usually dangling. `sdmx.agency` is keyed "
        "(scheme_urn, agency_id); the artefact carries only the id, not which "
        "AgencyScheme defines it, so there is half a key to join on. And the "
        "rows are absent unless an AgencyScheme was loaded — adding a codelist "
        "maintained by ESTAT creates no agency row. An edge would still "
        "*work* for `.agency.id`, since trimming rewrites it back to this "
        "column, while `.agency.name['en']` returned NULL almost always: an "
        "affordance that lies is worse than a plain column."
    ),
    ("hierarchy_level", "codelist_urn"): (
        "always NULL. A `Level.coding_format` is a plain Facet — SDMX gives a "
        "level no enumerated arm — so the column exists only because the whole "
        "facet group is interpolated from one definition (`_FACET_COLUMNS`), "
        "which is what stopped the three tables carrying it from drifting a "
        "third time. Wiring an edge to a column nothing ever writes would "
        "offer `.codelist` on a level and answer NULL for every row (#160)."
    ),
    ("annotation", "owner_artefact_urn"): (
        "a denormalisation of the hub below, carried for the delete cascade "
        "rather than for traversal (#145). `owner_urn` already reaches all "
        "four things an annotation can hang off, and this column would be a "
        "fifth edge into `artefact` — duplicating `owner_urn -> artefact` for "
        "a maintainable's own annotations and colliding with its `annotations` "
        'inverse name. Wire it if a caller wants "every annotation under this '
        "codelist, its codes' included\", which is the one question the hub "
        "genuinely cannot answer; nothing asks it today."
    ),
    ("artefact", "resource_id"): "the artefact's own identifier",
    ("component_map", "representation_map_urn"): (
        "polymorphic (#422): a RepresentationMap, or an epoch or date-pattern map inside a StructureMap, "
        "which is a row of `epoch_map` / `date_pattern_map` keyed by its map rather than a URN"
    ),
    ("organisation_contact", "contact_id"): "a contact's user id (#422), naming nothing this store holds",
    **dict.fromkeys(
        (("concept", "iso_concept_id"), ("concept", "iso_concept_scheme_id")),
        "an ISO 11179 registry's identifiers (#422) — a reference out of SDMX, to nothing this store holds",
    ),
    ("annotation", "annotation_id"): "the annotation's own identifier",
    ("category", "category_id"): "the category's own identifier",
    ("code", "code_id"): "the code's own identifier",
    ("concept", "concept_id"): "the concept's own identifier",
    ("hierarchy_node", "node_id"): "the node's own identifier",
    ("metadata_attribute", "attribute_id"): "the attribute's own identifier",
    ("agency", "agency_id"): "the agency's own identifier within its scheme",
    ("hierarchy_applicability", "agency_id"): (
        "the same id `artefact.agency_id` carries, projected onto the view so "
        "a derived scan bounds by agency the way a maintainable root does — "
        "and under-determined for exactly the reason above."
    ),
    ("data_provider", "provider_id"): "the provider's own identifier within its scheme",
    # `metadata_provision_agreement.metadata_provider_urn` was here, with the
    # reason "points at a relation that does not exist". #315 built the
    # relation — `MetadataProvider`, `MetadataProviderScheme`, the
    # `metadata_provider` table and its writer — so the column is now a
    # declared `Edge` and the entry is gone rather than reworded.
    ("metadata_provider", "provider_id"): "the provider's own identifier within its scheme",
    ("data_consumer", "consumer_id"): "the consumer's own identifier within its scheme",
    ("organisation_unit", "unit_id"): "the unit's own identifier within its scheme; `parent_id` is the edge",
    # The two item-map pair columns name items *within* the endpoint the map
    # points at — `ItemMap` carries ids, not urns, which is all SDMX puts in
    # it. The traversal a caller wants is `concept_scheme_map.source` to the
    # scheme and then its concepts; an edge here would need the endpoint's urn
    # to resolve against and does not have it (#395). A component map's ids are
    # the same, and lists since #422, so no `_id` column is left to list.
    ("item_map", "source_id"): "an item id within the map's source scheme, not a urn",
    ("item_map", "target_id"): "an item id within the map's target scheme, not a urn",
}


@pytest.mark.parametrize(
    ("table", "column"),
    sorted(_reference_shaped_columns()),
    ids=lambda v: v,
)
def test_every_reference_shaped_column_is_an_edge_or_has_a_reason(table: str, column: str) -> None:
    """A `*_urn` / `*_id` column on an exposed relation is a traversal or a
    written decision — never neither.

    The failure this catches is silent by construction: an un-wired reference
    is not an error, it is an absence, and absences do not fail tests. Both
    known cases were found by inventory rather than by use.
    """
    declared = {(edge.source, pair[0]) for edge in EDGES for pair in edge.columns}
    if (table, column) in declared:
        return
    assert (table, column) in NOT_EDGES, (
        f"{table}.{column} looks like a reference but is neither a declared "
        f"Edge nor listed in NOT_EDGES. Wire it, or add it with the reason."
    )


def test_the_not_edges_inventory_has_no_stale_entries() -> None:
    """An exemption that outlives its column is an exemption nobody re-reads."""
    live = _reference_shaped_columns()
    stale = {key for key in NOT_EDGES if key not in live}
    assert not stale, f"NOT_EDGES names columns that no longer exist: {sorted(stale)}"


def test_a_hub_column_is_declared_once_per_target() -> None:
    """`annotation.owner_urn` points at four relations, and each is its own edge.

    One polymorphic edge would make everything reachable from everything, which
    is the measurement that made bare column references unresolvable in the
    first place. Typed edges keep the graph honest: there is no path from a
    code to a DSD through annotations.

    The set is asserted whole rather than as a subset, so a *new* annotated
    relation has to be added here deliberately — which is how the hierarchy
    node came to be in it.
    """
    hub = [e for e in EDGES if e.source == "annotation" and e.column == "owner_urn"]
    assert {e.target for e in hub} == {"artefact", "code", "concept", "hierarchy_node"}
    assert all(e.name is None for e in hub)
    assert all(e.inverse == "annotations" for e in hub)


# ── edge targets are declared, not inherited from file order (#352) ──────────


def _declared_edges() -> list[tuple[str, str, type]]:
    """(owner class, attribute, resolved target class) for every `edge()`."""
    from sdmxlib.query import accessors
    from sdmxlib.query._expr import _LazyEdge

    found = []
    for owner_name in dir(accessors):
        owner = getattr(accessors, owner_name)
        if not isinstance(owner, type) or not owner_name.startswith("_"):
            continue
        for attr, value in vars(owner).items():
            if isinstance(value, _LazyEdge):
                found.append((owner_name, attr, type(getattr(owner(()), attr))))
    return found


#: Edges that legitimately land on any maintainable. Everything else must name
#: a concrete accessor — the list is short and each entry is a decision.
_POLYMORPHIC = {
    ("_MetadataTarget", "artefact"),  # reference metadata describes anything
    ("_Categorisation", "artefact"),  # a category can categorise anything
    ("_MetadataSet", "structure"),  # Metadataflow | MetadataProvisionAgreement
    ("_HierarchyAssociation", "linked_object"),
    ("_HierarchyAssociation", "context_object"),
    # ConceptSchemeMap | CategorySchemeMap. One `item_map` table serves both,
    # and both live in `artefact`, so the hop back cannot name either without
    # excluding the other — rooting at `q.concept_scheme_map` or
    # `q.category_scheme_map` is what picks a kind (#395).
    ("_ItemMap", "scheme_map"),
    # Any organisation scheme, some of them unmodelled, so no one accessor (#448).
    ("_OrganisationSchemeMap", "source"),
    ("_OrganisationSchemeMap", "target"),
}


def test_no_edge_falls_back_to_maintainable_by_accident() -> None:
    """`_Maintainable` must be a *choice*, not what an edge got because its
    target was defined further down the file.

    Before #352 seventeen edges landed here and the set was decided by class
    order: `q.dsd.components.concept` continued because `_Concept` precedes
    `_Component`, while `q.dsd.components.codelist` stopped because `_Codelist`
    follows it. Same relation, one hop, opposite outcomes, and no test could
    have told you — which also meant reordering the file for readability
    silently changed the public language.
    """
    from sdmxlib.query.accessors import _Maintainable

    fell_back = {
        (owner, attr)
        for owner, attr, target in _declared_edges()
        if target is _Maintainable and (owner, attr) not in _POLYMORPHIC
    }
    assert not fell_back, (
        f"these edges land on _Maintainable without being declared polymorphic: {sorted(fell_back)}. "
        "Name the real accessor in the edge, or add it to _POLYMORPHIC with a reason."
    )


def test_every_polymorphic_edge_still_exists() -> None:
    """The other direction: an exemption for an edge that is gone, or that has
    since been given a concrete target, is a stale licence to fall back."""
    declared = {(owner, attr) for owner, attr, _ in _declared_edges()}
    assert declared >= _POLYMORPHIC, f"exemptions for edges that no longer exist: {sorted(_POLYMORPHIC - declared)}"

    from sdmxlib.query.accessors import _Maintainable

    concrete = {(owner, attr) for owner, attr, target in _declared_edges() if target is not _Maintainable}
    assert not (_POLYMORPHIC & concrete), (
        f"these are exempted but have a concrete target: {sorted(_POLYMORPHIC & concrete)} — drop the exemption"
    )


def test_an_edge_target_may_be_declared_before_it_exists() -> None:
    """The mechanism, stated as the property that justifies it.

    The point of naming the target is that declaration order stops mattering.
    `_Component` is defined *above* `_Codelist`, so this hop could not have
    been typed at all under the old scheme.
    """
    from sdmxlib.query import accessors

    assert accessors.__dict__["_Component"].__qualname__
    source = pathlib.Path(accessors.__file__).read_text(encoding="utf-8")
    assert source.index("class _Component") < source.index("class _Codelist"), (
        "fixture assumption: _Component is declared before _Codelist"
    )
    assert type(accessors.q.dsd.components.codelist).__name__ == "_Codelist"


def test_an_unresolvable_edge_target_says_so() -> None:
    """A typo in the target name must fail at the read, naming the problem —
    resolving by string is what makes that possible to get wrong."""
    from sdmxlib.query._expr import _LazyEdge
    from sdmxlib.query._joins import PathError

    class _Holder:
        broken = _LazyEdge(("x",), "_NoSuchAccessor")

    with pytest.raises(PathError, match="_NoSuchAccessor"):
        _ = _Holder().broken
