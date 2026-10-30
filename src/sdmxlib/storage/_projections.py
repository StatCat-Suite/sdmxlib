"""Low-level DuckDB SQL projections + per-thread cursor.

A leaf module (stdlib, duckdb and `sdmxlib.storage.schema`, itself a leaf) so both
`sdmxlib.storage.readers` and `sdmxlib.storage.lazy` can depend on it
without importing each other. Extracted from `readers` to break the
``readers`` ⇄ ``lazy`` import cycle: ``readers`` constructs Lazy facades
(imports ``lazy``) while ``lazy`` runs the ``code_map`` projection — which
now lives here rather than in ``readers``.
"""

import threading
import weakref

import duckdb

from sdmxlib.model.istring import normalize_tag
from sdmxlib.storage.schema import sdmx_cursor

# Per-thread cursor store for the projections below (#80). Held weakly so a
# closed Registry's connection doesn't pin a stale cursor entry (avoids the
# id-reuse hazard a plain id-keyed dict would have when Python recycles the
# connection's id after GC).
_thread_local = threading.local()


def _thread_cursor(conn_or_cur: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    table = getattr(_thread_local, "table", None)
    if table is None:
        table = weakref.WeakKeyDictionary()
        _thread_local.table = table
    cur = table.get(conn_or_cur)
    if cur is None:
        cur = sdmx_cursor(conn_or_cur)
        table[conn_or_cur] = cur
    return cur


def code_map(
    conn: duckdb.DuckDBPyConnection,
    codelist_urn: str,
    *,
    lang: str,
) -> dict[str, str]:
    """Return ``{code_id: label}`` for a codelist via direct SQL.

    Uses MAP column subscript — works for any language without
    upfront configuration. Opens its own per-call cursor (issue #75):
    safe to call concurrently from threads sharing the same connection.

    **Every code appears, labelled by its id when it has no name in `lang`.**
    This used to drop those rows, which the eager `Codelist.code_map` never
    did — so the same call meant two things depending on which class a caller
    held, and the SQL one silently shrank the map. `sdmxlib.polars.label` builds a
    `replace_strict` from it, so a dropped code became a null column value
    rather than a visible failure (#398).

    Raises:
        ValueError: If `lang` is not a well-formed language tag.
    """
    lang = normalize_tag(lang)
    cur = _thread_cursor(conn)
    rows = cur.execute(
        """
        SELECT code_id, coalesce(name[?], code_id) AS label
        FROM sdmx.code
        WHERE codelist_urn = ?
        ORDER BY position
        """,
        [lang, codelist_urn],
    ).fetchall()
    return {r[0]: r[1] for r in rows}
