"""`hierarchy.ancestor_at(level)` — every node mapped to its nearest ancestor.

The central query in hierarchical analysis: totalling observations by province
needs each geography mapped to the province containing it. Applications were
building it by hand — traverse the tree in Python, materialise a dict, register
a view, join, unregister — with their own cache around it (#274).

Its own module because it survived §3. `HierarchyNodeQuery` was retired in
favour of the path language, and this went with neither: it is a *mapping*
relation rather than a filter, so no path expresses it, and it never used the
predicate machinery that was retired — one query over the ancestor paths the
storage writer already bakes.
"""

import attrs
import duckdb
import polars as pl

from sdmxlib.session import cursor_for
from sdmxlib.sql import SqlRelation


@attrs.frozen
class AncestorMap:
    """Every node mapped to its nearest ancestor at a `Level`, as a relation.

    The central query in hierarchical analysis: to total observations by
    province you need each geography mapped to the province containing it.
    Applications were building this by hand — traverse the tree in Python,
    materialise a dict, register it as a DuckDB view, join, unregister — with
    their own cache around it (#274). It is one query over the ancestor paths
    the storage writer already bakes.

    Keyed by path, ``(path, node_id, rollup_path, rollup_id)``: a node id may
    repeat in another branch (#439), so a mapping keyed by id would give one
    node two images. The ids ride along because they are what a join against
    observation codes uses. A *join* input rather than an ``IN`` list;
    `ancestors` narrows it to the rollup ids for the membership case.

    Semantics follow `Hierarchy.inherit_along_broader`, which is the in-memory
    equivalent and the oracle the property tests compare against:

    - a node **at** the level maps to itself,
    - a node below maps to its **nearest** ancestor at that level,
    - a node with no such ancestor is **absent**, not mapped to ``NULL``.

    Example:
        rollup = h.ancestor_at("PR")
        rollup.to_dict()          # {"CA.ON.ON-01": "CA.ON", "CA.ON": "CA.ON", ...}
        rollup.collect()          # pl.DataFrame
    """

    hierarchy_urn: str
    level_id: str
    _conn: duckdb.DuckDBPyConnection = attrs.field(eq=False, repr=False, alias="conn")
    #: Whether a node already *at* the level maps to itself. Defaults to True
    #: here and False on the transitive walks, because the operations differ:
    #: a closure is a set of other nodes, this is a total mapping, and a
    #: mapping that drops the identity is undefined for every node at the
    #: level — a province with no province.
    include_self: bool = True

    @property
    def columns(self) -> tuple[str, ...]:
        """``(path, node_id, rollup_path, rollup_id)`` — see `sdmxlib.sql.SqlRelation`."""
        return ("path", "node_id", "rollup_path", "rollup_id")

    def to_sql(self) -> tuple[str, list[object]]:
        """Compile without executing, for use as a join input or subquery.

        Self and ancestors are unioned and the greatest ``depth`` wins, which
        is what makes the match the *nearest* one — and makes a node at the
        level beat its own ancestors, since it is deepest.
        """
        sql = """
            SELECT path, node_id, rollup_path, rollup_id FROM (
                SELECT path, node_id, rollup_path, rollup_id,
                       ROW_NUMBER() OVER (PARTITION BY path ORDER BY anc_depth DESC) AS rn
                FROM (
                    SELECT hn.path, hn.node_id, hn.path AS rollup_path, hn.node_id AS rollup_id,
                           hn.depth AS anc_depth
                    FROM sdmx.hierarchy_node hn
                    WHERE hn.hierarchy_urn = ? AND hn.level_id = ? AND ?
                    UNION ALL
                    SELECT hn.path, hn.node_id, anc.path, anc.node_id, anc.depth
                    FROM sdmx.hierarchy_node hn, UNNEST(hn.ancestor_paths) AS t(anc_path)
                    JOIN sdmx.hierarchy_node anc
                      ON anc.hierarchy_urn = hn.hierarchy_urn AND anc.path = t.anc_path
                    WHERE hn.hierarchy_urn = ? AND anc.level_id = ?
                )
            ) WHERE rn = 1
        """
        return sql, [self.hierarchy_urn, self.level_id, self.include_self, self.hierarchy_urn, self.level_id]

    def ancestors(self) -> SqlRelation:
        """The distinct rollup targets, as a one-column relation.

        For the membership case — ``code.id.is_in(h.ancestor_at("PR").ancestors())``
        — where the two-column mapping is not what ``IN`` wants. Typed as the
        public `sdmxlib.SqlRelation` it is, not the private class behind it,
        so a caller can annotate what they hold (#513).
        """
        return _AncestorTargets(self)

    def collect(self) -> pl.DataFrame:
        """Execute and return the mapping as a Polars frame."""
        sql, params = self.to_sql()
        return cursor_for(self._conn).execute(sql, params).pl()

    def to_dict(self) -> dict[str, str]:
        """Execute and return ``{path: rollup_path}``.

        The eager form, matching what `Hierarchy.inherit_along_broader`
        returns — both keyed by path. Prefer `to_sql` or `ancestors` where the
        mapping can stay in the database.
        """
        sql, params = self.to_sql()
        return {r[0]: r[2] for r in cursor_for(self._conn).execute(sql, params).fetchall()}


@attrs.frozen
class _AncestorTargets:
    """One-column projection of a `AncestorMap`, for ``IN`` membership."""

    rollup: AncestorMap

    @property
    def columns(self) -> tuple[str, ...]:
        return ("rollup_id",)

    def to_sql(self) -> tuple[str, list[object]]:
        # `inner` is `AncestorMap.to_sql`, which is a constant template with
        # its own values bound — nothing from a caller is interpolated. S608
        # cannot see that across the call, and there is no bind form for a
        # subquery.
        inner, params = self.rollup.to_sql()
        return f"SELECT DISTINCT rollup_id FROM ({inner})", params  # noqa: S608


__all__ = ["AncestorMap"]
