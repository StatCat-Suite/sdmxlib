"""Resolving paths to hops, and hops to a join tree.

A path is a tuple of attribute names — `("concept", "codelist")` — and means
nothing on its own. Resolving it against the edge graph turns it into a
sequence of `Hop`s, each naming the edge walked and the direction walked in.
That resolution is also the point at which an undeclared route is caught, which
is what makes a path a proof rather than a hope.

**Shared prefixes are deduplicated, and that is a requirement rather than an
optimisation.** Two paths off one root — `dimensions.concept.id` and
`dimensions.codelist.id` — must produce *one* join to `dsd_component`, not two.
The correlated-subquery benchmark in `docs/design/evidence/query-language.md` measured the
cost of getting this wrong as linear in the number of fan-out columns.
"""

from collections.abc import Iterable, Sequence

import attrs

from sdmxlib.query._edges import EDGES, FACETS, Edge, Facet


class PathError(ValueError):
    """A path names a hop the edge graph does not declare."""


@attrs.frozen
class Hop:
    """One resolved step along a path.

    Attributes:
        name: The attribute as written by the caller.
        edge: The declared edge walked.
        forward: `True` when walking source to target — the to-one direction,
            a plain join. `False` when walking target to source, which is
            to-many and needs a correlated subquery rather than a join.
        facet: Set when the hop is a pre-filtered view of the edge, carrying
            the constant to apply to the child.
    """

    name: str
    edge: Edge
    forward: bool
    facet: Facet | None = None

    @property
    def table(self) -> str:
        """Relation this hop arrives at."""
        return self.edge.target if self.forward else self.edge.source

    def condition(self, outer: str, inner: str) -> str:
        """The join or correlation condition for this hop, in its direction.

        `outer` is where the hop starts and `inner` where it arrives, which is
        the source/target pair either way round depending on `forward`.
        """
        if self.forward:
            return self.edge.condition(source_alias=outer, target_alias=inner)
        return self.edge.condition(source_alias=inner, target_alias=outer)

    @property
    def to_many(self) -> bool:
        """Whether this hop can change the row count."""
        # A *membership* reference is to-many in the forward direction too:
        # one row's list matches many target rows, whatever the target key
        # says. Deriving it from key uniqueness alone would call
        # `code -> ancestors` to-one, which is the one shape the derivation
        # cannot see — so it is read off the declaration instead.
        return not self.forward or self.edge.member_of is not None


def _hops_available(table: str) -> list[str]:
    names = [e.name for e in EDGES if e.source == table and e.name]
    inverses = [e.inverse for e in EDGES if e.target == table and e.inverse]
    facets = [f.name for f in FACETS if f.base in inverses]
    return sorted({*(n for n in names if n), *(i for i in inverses if i), *facets})


def resolve_path(root_table: str, path: Sequence[str]) -> tuple[Hop, ...]:
    """Turn attribute names into declared hops, or say why they are not.

    Args:
        root_table: Relation the query is rooted at.
        path: Attribute names as written, outermost first.

    Raises:
        PathError: When a name is not a declared hop from the current relation.
            The message lists what *is* reachable, because a caller who
            mistyped a hop needs the alternatives more than the rule.
    """
    hops: list[Hop] = []
    table = root_table
    for name in path:
        hop = _resolve_one(table, name)
        if hop is None:
            available = _hops_available(table)
            msg = f"{table!r} has no hop named {name!r}. Reachable from here: {', '.join(available) or '(none)'}."
            raise PathError(msg)
        hops.append(hop)
        table = hop.table
    return tuple(hops)


def trim(root_table: str, path: tuple[str, ...], table: str, column: str) -> tuple[tuple[str, ...], str, str]:
    """Drop a hop that only recovers a key the source row already stores.

    `code.parent.id` and `code.parent_id` ask the same question: the foreign
    key *is* the target's key, so resolving the row to read it back is work
    with no answer in it. This rewrites the first spelling into the second,
    which removes a join and — because it runs before anything downstream sees
    the column — makes the two spellings **one query** rather than two: same
    SQL, same `canonical_key`, same plan.

    Django does exactly this and calls it join trimming; `parent__id=1` never
    emits a join. It is why the pair is safe to expose there, while SQLAlchemy,
    which does not trim, instead keeps the spellings far apart (`parent_id ==`
    versus `parent.has(...)`). We expose Django's surface, so we owe Django's
    compiler. See `docs/design/query-tree-paths.md` §3.

    Resolution goes through `resolve_path` from the root rather than matching
    the edge by name, because a forward name is *not* unique on its own: six
    triples of `(name, target, target_column)` are shared by two or more edges
    — `codelist` is declared on `code`, `dsd_component` and `concept`, all
    reaching `artefact.urn`. Matching by name would have to decline on every
    one of them, which is to say on almost every edge that is not a tree.

    Only a **forward, to-one, non-membership** hop trims. A reverse hop's key
    lives on the far row, so there is nothing local to fall back to; a
    membership hop's source column is a list of keys rather than one.

    The rewrite is *not* semantics-preserving on a dangling reference: joined,
    `parent.id == "GHOST"` finds nothing because no row resolves; trimmed, it
    finds the row that stores the reference. That is only reachable by breaking
    referential integrity behind the writer's back, and taking the trimmed
    answer is what makes the two spellings inseparable — the point of the
    exercise.

    Args:
        root_table: Relation the path starts from.
        path: Declared hop names from the root, outermost first.
        table: Relation the column currently sits on.
        column: Physical column name.

    Returns:
        The rewritten `(path, table, column)`, or the input unchanged when no
        hop qualifies. Applied repeatedly, so `parent.parent.id` collapses to
        one hop rather than only shedding the outer one.
    """
    while path:
        try:
            hop = resolve_path(root_table, path)[-1]
        except PathError:
            # Not this function's error to report. An unresolvable path still
            # has to reach the compiler, which raises with the reachable hops
            # listed; raising here would move that message to attribute-access
            # time and give a worse one. Declining leaves it exactly where it
            # was.
            return path, table, column
        if not hop.forward or hop.edge.member_of is not None:
            return path, table, column
        sources = [source for source, target in hop.edge.columns if target == column]
        if len(sources) != 1:
            return path, table, column
        path, table, column = path[:-1], hop.edge.source, sources[0]
    return path, table, column


def _resolve_one(table: str, name: str) -> Hop | None:
    for edge in EDGES:
        if edge.source == table and edge.name is not None and edge.name == name:
            return Hop(name, edge, forward=True)
    for edge in EDGES:
        if edge.target == table and edge.inverse == name:
            return Hop(name, edge, forward=False)
    for facet in FACETS:
        if facet.name != name:
            continue
        for edge in EDGES:
            if edge.target == table and edge.inverse == facet.base:
                return Hop(name, edge, forward=False, facet=facet)
    return None


#: Schema holding every structural table, and the only place that name is
#: written. It was a literal in three renderings, which read as a formatting
#: detail and was actually an assumption: that everything this compiler can
#: reach lives in one schema of ours. A dataflow's observations do not — they
#: are `memory.data.obs`, or a DuckLake catalog — and #297's data-plane
#: vocabulary needs the same compiler over them.
STRUCTURAL_SCHEMA = "sdmx"


def qualify(schema: str | None, table: str) -> str:
    """A table as it appears in `FROM`. `None` means it is qualified already."""
    return table if schema is None else f"{schema}.{table}"


@attrs.frozen
class Join:
    """One join in the plan, already aliased.

    Two join shapes, and the second has no caller yet. A declared hop always
    joins `ON` an expression the edge supplies; the data plane's
    series-attributes sidecar joins `USING` the columns common to both tables,
    which `SchemaInspector.plan` already derives. Modelling it here rather than
    pre-rendering a string is #297 step 2 — see `docs/design/data-plane-query.md`
    — and it is staged before the root that will use it because everything it
    touches is structural code with structural tests over it.

    Attributes:
        alias: This relation's alias in the statement.
        table: Table name, qualified by `schema` unless that is `None`.
        optional: `LEFT JOIN` rather than `JOIN`.
        on: Join condition, for the `ON` shape.
        using: Common columns, for the `USING` shape.
        schema: Schema qualifying `table`. A *hop* is always structural —
            every declared edge targets a table in `EDGES` and those are all
            `sdmx` — so this only moves for a join the data plane adds.
    """

    alias: str
    table: str
    optional: bool
    on: str = ""
    using: tuple[str, ...] = ()
    schema: str | None = STRUCTURAL_SCHEMA

    def __attrs_post_init__(self) -> None:
        if bool(self.on) == bool(self.using):
            got = "both" if self.on else "neither"
            msg = f"a join is `ON` an expression or `USING` columns, not {got} ({self.alias} -> {self.table})"
            raise ValueError(msg)

    def to_sql(self) -> str:
        kind = "LEFT JOIN" if self.optional else "JOIN"
        table = qualify(self.schema, self.table)
        if self.using:
            return f"{kind} {table} {self.alias} USING ({', '.join(self.using)})"
        return f"{kind} {table} {self.alias} ON {self.on}"


@attrs.frozen
class RootJoin:
    """A join the root always has, declared without an alias.

    A *hop* is resolved from the edge graph, which describes tables this
    library owns the schema of. This is the other kind: a dataflow's
    series-attributes sidecar, which comes from a binding, is discovered at
    runtime, and joins on the columns the two tables share (#332).

    No alias, because the scope hands those out — a correlated subquery must
    not collide with the enclosing query's. `_Scope` turns this into a `Join`
    once it has a namespace.

    Attributes:
        table: Fully-qualified table, so it needs no schema of its own.
        using: Columns common to both sides, in the main table's order.
    """

    table: str
    using: tuple[str, ...]


@attrs.frozen
class KeyRank:
    """Where a root key column's values take their position: one item scheme's rows.

    A dataflow's dimension enumerated by a codelist holds code ids, and the
    codelist orders them by `position`. The root's natural order sorts each such
    column by that position, with the id as the tiebreak (GH-213). The scheme's
    items live in a structural table keyed by scheme and item id, so the order
    joins it once per column, `LEFT` so a value the scheme does not list keeps
    its row and sorts after every listed one.

    Attributes:
        column: The root key column, as stored.
        table: The structural table holding the scheme's items: `code` or
            `value_item`.
        scheme_column: That table's column naming the scheme.
        item_column: That table's column holding the item id.
        scheme: The scheme's URN, as the store keys it. Bound as a parameter.
    """

    column: str
    table: str
    scheme_column: str
    item_column: str
    scheme: str


@attrs.frozen
class JoinPlan:
    """The `FROM` clause for a set of to-one paths, with aliases assigned."""

    root_alias: str
    root_table: str
    joins: tuple[Join, ...]
    #: Alias reached by each path prefix, including the empty root prefix.
    alias_of: dict[tuple[str, ...], str]
    #: Schema qualifying `root_table`, or `None` when it is qualified already.
    #:
    #: `None` is the data-plane case: a binding names its table as
    #: `<database_role>.<table_location>`, which is a two-part name of its own
    #: and must not be prefixed again.
    root_schema: str | None = STRUCTURAL_SCHEMA

    @property
    def root_sql(self) -> str:
        """The root as it appears in `FROM`, qualified or not."""
        return qualify(self.root_schema, self.root_table)

    def to_sql(self) -> str:
        parts = [f"{self.root_sql} {self.root_alias}"]
        parts.extend(j.to_sql() for j in self.joins)
        return "\n".join(parts)


def plan_joins(
    root_table: str,
    paths: Iterable[Sequence[str]],
    *,
    root_alias: str = "t0",
    alias_prefix: str = "t",
    root_schema: str | None = STRUCTURAL_SCHEMA,
) -> JoinPlan:
    """Build one join per distinct to-one prefix across all `paths`.

    Deduplication is by prefix, so `("concept",)` and `("concept", "codelist")`
    share the join to `concept` rather than repeating it. Aliases are assigned
    in sorted prefix order, which keeps emitted SQL stable across runs — a
    changing alias would churn the `sql_corpus` for no reason.

    `root_alias` and `alias_prefix` let a caller place the plan in its own
    namespace, which is what keeps a correlated subquery's aliases from
    colliding with the enclosing query's.

    `root_schema` is `None` for a root that is qualified already — a
    dataflow's observation table, which no edge points at and which lives
    wherever its binding says.

    Raises:
        PathError: If any path crosses a to-many hop. Those are not joins; they
            compile to a correlated subquery, which the predicate compiler
            handles.
    """
    prefixes: set[tuple[str, ...]] = set()
    resolved: dict[tuple[str, ...], Hop] = {}
    for path in paths:
        hops = resolve_path(root_table, path)
        for depth, hop in enumerate(hops, start=1):
            prefix = tuple(path[:depth])
            if hop.to_many:
                msg = (
                    f"{'.'.join(prefix)!r} crosses a to-many edge, which is not "
                    f"a join — it compiles to a correlated subquery. Reach it "
                    f"through a fan-out predicate instead."
                )
                raise PathError(msg)
            prefixes.add(prefix)
            resolved[prefix] = hop

    alias_of: dict[tuple[str, ...], str] = {(): root_alias}
    joins: list[Join] = []
    for index, prefix in enumerate(sorted(prefixes), start=1):
        hop = resolved[prefix]
        alias = f"{alias_prefix}{index}"
        alias_of[prefix] = alias
        parent = alias_of[prefix[:-1]]
        on = hop.condition(outer=parent, inner=alias)
        joins.append(Join(alias, hop.table, on=on, optional=hop.edge.optional))

    return JoinPlan(root_alias, root_table, tuple(joins), alias_of, root_schema)
