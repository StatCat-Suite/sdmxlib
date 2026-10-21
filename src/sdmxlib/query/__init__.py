"""The query model — a typed expression language over the SDMX storage graph.

Separate from `sdmxlib.model`, deliberately. The domain model's `Ref` graph and
the storage join graph overlap without either containing the other: storage has
containment edges the model expresses as `ItemList` membership, side tables with
no domain counterpart, and different names where the two do correspond. Deriving
query edges from `Ref` fields would therefore be *wrong* rather than merely
redundant.

See `docs/design/query-language.md` for the design, and #306 for the milestone.
"""

from sdmxlib.query._edges import EDGES, FACETS, Edge, Facet, edges_from, edges_into, facets_of
from sdmxlib.query.query import union
from sdmxlib.query.raw import RawSql, RawSqlError
from sdmxlib.query.schema import schema

__all__ = [
    "EDGES",
    "FACETS",
    "Edge",
    "Facet",
    "RawSql",
    "RawSqlError",
    "edges_from",
    "edges_into",
    "facets_of",
    "schema",
    "union",
]
